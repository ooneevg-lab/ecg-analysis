"""
Session management endpoints.
"""
import threading
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ..routes.upload import (
    _load_metadata,
    _save_metadata,
    current_threshold,
    get_file_storage,
    get_session_store,
    get_storage_lock,
    set_current_threshold,
)

session_bp = Blueprint('session', __name__)


def _sanitize_rhythm(result: dict) -> tuple[str, str]:
    """Filter out obsolete or invalid rhythm classes from cached results.

    Unknown classes are returned unchanged (instead of being silently mapped
    to 'normal') so that new rhythm classes introduced in future versions
    are not lost in the UI.
    """
    rhythm_class = result.get('rhythm_class', '')
    rhythm_class_ru = result.get('rhythm_class_ru', '')

    if rhythm_class in ('normal', 'bradycardia', 'tachycardia', 'unknown', ''):
        return rhythm_class, rhythm_class_ru

    if rhythm_class and not rhythm_class_ru:
        return rhythm_class, rhythm_class
    return rhythm_class, rhythm_class_ru


@session_bp.route('/files', methods=['GET'])
def list_files():
    """
    List all files in current session with pagination.

    GET /api/session/files?page=1&per_page=50

    Returns:
        JSON with list of files and their status. Archived files are
        hidden from the list and reported separately via ``archive_count``.
        Pagination info: page, per_page, total, pages.
    """
    storage = get_file_storage()

    # Pagination parameters
    try:
        page = int(request.args.get('page', 1))
        page = max(1, page)
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.args.get('per_page', 50))
        per_page = min(max(1, per_page), 200)  # Cap at 200
    except (TypeError, ValueError):
        per_page = 50

    files = []
    archive_count = 0
    with get_storage_lock():
        snapshot = list(storage.items())
    for file_id, data in snapshot:
        if data.get('archived'):
            archive_count += 1
            continue
        file_info = {
            'file_id': file_id,
            'filename': data['filename'],
            'is_valid': data['is_valid'],
            'validation_errors': data.get('validation_errors', []),
            'parse_error': data.get('parse_error'),
            'analyzed': data['analyzed'],
            'viewed': data.get('viewed', False),
            'created_at': data.get('created_at'),
            'source': data.get('source', 'upload'),
            'source_path': data.get('source_path'),
            'auto_imported': bool(data.get('auto_imported', False)),
        }

        # Add analysis info if available
        if data['analyzed'] and data['analysis_result']:
            result = data['analysis_result']
            rhythm_class, rhythm_class_ru = _sanitize_rhythm(result)
            file_info['probability'] = result.get('probability', 0)
            file_info['pathology_probability'] = result.get('pathology_probability', result.get('probability', 0))
            file_info['confidence'] = result.get('confidence', 0)
            file_info['class_probability'] = result.get('class_probability', result.get('confidence', 0))
            file_info['prediction'] = result.get('prediction', 0)
            file_info['verdict'] = result.get('verdict', '')
            file_info['class_names'] = result.get('class_names', {})
            file_info['probabilities'] = result.get('probabilities', {})
            file_info['argmax_label'] = result.get('argmax_label')
            file_info['argmax_class'] = result.get('argmax_class', '')
            file_info['pred_label'] = result.get('pred_label')
            file_info['pred_class'] = result.get('pred_class', '')
            file_info['threshold_applied'] = result.get('threshold_applied', False)
            file_info['threshold_rule'] = result.get('threshold_rule')
            file_info['threshold'] = result.get('threshold')
            file_info['rhythm_class'] = rhythm_class
            file_info['rhythm_class_ru'] = rhythm_class_ru
            file_info['hr_bpm'] = result.get('hr_bpm', 0)

        files.append(file_info)

    # Default server order: newest records first, with analyzed records first
    # inside the same timestamp. The UI can still apply its own sort mode.
    files.sort(key=lambda x: (
        x.get('created_at') or '',
        x['analyzed'],
        x.get('probability', 0),
    ), reverse=True)

    # Pagination
    total = len(files)
    pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated_files = files[start:end]

    return jsonify({
        'success': True,
        'files': paginated_files,
        'count': len(paginated_files),
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': pages,
        'archive_count': archive_count,
        'threshold': current_threshold(),
    })
@session_bp.route('/clear', methods=['POST'])
def clear_session():
    """
    Clear all files from session.

    POST /api/session/clear
    """
    storage = get_file_storage()

    # Delete ALL XML files in UPLOAD_FOLDER (single user)
    upload_folder = Path(current_app.config.get('UPLOAD_FOLDER_RESOLVED', current_app.config['UPLOAD_FOLDER']))
    if upload_folder.exists():
        for xml_file in upload_folder.glob('*.xml'):
            try:
                xml_file.unlink(missing_ok=True)
            except Exception:
                pass

    # Clear storage
    with get_storage_lock():
        storage.clear()
    get_session_store().clear_records()

    # Drop persisted metadata (viewed + archive flags) so a fresh session
    # is truly clean.
    meta_path = upload_folder / '.ecg_metadata.json'
    if meta_path.exists():
        try:
            meta_path.unlink()
        except Exception:
            pass

    return jsonify({
        'success': True,
        'message': 'Сессия очищена'
    })


@session_bp.route('/threshold', methods=['GET', 'POST'])
def threshold_setting():
    """Get or update the single classification threshold used by UI and watcher."""
    if request.method == 'GET':
        return jsonify({'success': True, 'threshold': current_threshold()})

    params = request.get_json(silent=True) or {}
    try:
        threshold = float(params.get('threshold'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Некорректный порог'}), 400
    if threshold < 0.0 or threshold > 1.0:
        return jsonify({'success': False, 'error': 'Порог должен быть от 0 до 1'}), 400
    threshold = set_current_threshold(threshold)
    return jsonify({'success': True, 'threshold': threshold})


@session_bp.route('/archive', methods=['GET'])
def list_archive():
    """
    List archived files. Records enter the archive only by explicit user action.

    GET /api/session/archive

    Returns a minimal record per file — no parsed signal, just metadata
    and the cached analysis result for browsing.
    """
    storage = get_file_storage()

    with get_storage_lock():
        snapshot = list(storage.items())
    files = []
    for file_id, data in snapshot:
        if not data.get('archived'):
            continue
        info = {
            'file_id': file_id,
            'filename': data['filename'],
            'viewed': data.get('viewed', False),
            'analyzed': data['analyzed'],
            'created_at': data.get('created_at'),
            'source': data.get('source', 'upload'),
            'source_path': data.get('source_path'),
            'auto_imported': bool(data.get('auto_imported', False)),
            'can_open_analysis': bool(data.get('parsed') is not None),
        }
        source_path = data.get('source_path')
        if source_path:
            try:
                info['can_open_analysis'] = Path(source_path).is_file()
            except Exception:
                pass
        if data['analyzed'] and data['analysis_result']:
            result = data['analysis_result']
            rhythm_class, rhythm_class_ru = _sanitize_rhythm(result)
            info['probability'] = result.get('probability', 0)
            info['pathology_probability'] = result.get('pathology_probability', result.get('probability', 0))
            info['verdict'] = result.get('verdict', '')
            info['pred_class'] = result.get('pred_class', '')
            info['rhythm_class'] = rhythm_class
            info['rhythm_class_ru'] = rhythm_class_ru
            info['hr_bpm'] = result.get('hr_bpm', 0)
        files.append(info)

    # Newest archived first.
    files.sort(key=lambda x: x.get('created_at') or '', reverse=True)
    return jsonify({'success': True, 'files': files, 'count': len(files)})


@session_bp.route('/restore/<file_id>', methods=['POST'])
def restore_from_archive(file_id: str):
    """
    Restore a file from the archive back to the active list.

    POST /api/session/restore/<file_id>
    """
    from ..routes.upload import set_file_archived
    if not set_file_archived(file_id, False):
        return jsonify({'success': False, 'error': 'Файл не найден'}), 404
    return jsonify({'success': True, 'archived': False})


@session_bp.route('/archive/<file_id>', methods=['POST'])
def archive_file(file_id: str):
    """Move a file to archive by explicit user action."""
    from ..routes.upload import set_file_archived
    if not set_file_archived(file_id, True):
        return jsonify({'success': False, 'error': 'Р¤Р°Р№Р» РЅРµ РЅР°Р№РґРµРЅ'}), 404
    return jsonify({'success': True, 'archived': True})


@session_bp.route('/archive/bulk', methods=['POST'])
def archive_bulk():
    """Archive multiple files by explicit user action."""
    from ..routes.upload import set_file_archived
    params = request.get_json(silent=True) or {}
    file_ids = params.get('file_ids') or []
    changed = 0
    for file_id in file_ids:
        if set_file_archived(str(file_id), True):
            changed += 1
    return jsonify({'success': True, 'changed': changed})


@session_bp.route('/restore/bulk', methods=['POST'])
def restore_bulk():
    """Restore multiple files from archive."""
    from ..routes.upload import set_file_archived
    params = request.get_json(silent=True) or {}
    file_ids = params.get('file_ids') or []
    changed = 0
    for file_id in file_ids:
        if set_file_archived(str(file_id), False):
            changed += 1
    return jsonify({'success': True, 'changed': changed})


@session_bp.route('/archive/<file_id>', methods=['DELETE'])
def delete_archived_file(file_id: str):
    """Permanently delete a file from archive.

    DELETE /api/session/archive/<file_id>

    Removes the file from _file_storage, .ecg_metadata.json, and .watched_state.json if applicable.
    """
    from ..routes.upload import remove_file_record, get_session_store
    storage = get_file_storage()

    if file_id not in storage:
        return jsonify({'success': False, 'error': 'Файл не найден'}), 404

    file_data = storage[file_id]
    if not file_data.get('archived'):
        return jsonify({'success': False, 'error': 'Файл не в архиве'}), 400

    # Delete permanent XML file if it exists
    source_path = file_data.get('source_path')
    if source_path:
        try:
            Path(source_path).unlink(missing_ok=True)
        except Exception:
            pass

    # Remove from watcher state if it was watcher-imported
    if source_path and file_data.get('source') == 'watcher':
        watcher = current_app.extensions.get('watcher')
        if watcher is not None and hasattr(watcher, '_state') and watcher._state is not None:
            try:
                # Find the watcher state key (original watched folder path) by file_id
                watcher_key = None
                for key in watcher._state.all_keys():
                    entry = watcher._state.get(key)
                    if entry and str(entry.get('file_id') or '') == file_id:
                        watcher_key = key
                        break
                if watcher_key:
                    watcher._state.remove(watcher_key)
            except Exception:
                pass

    # Remove from storage and metadata
    remove_file_record(file_id)

    # Also clean up .ecg_metadata.json viewed/archived entries
    try:
        metadata = _load_metadata()
        metadata.get('viewed', {}).pop(file_id, None)
        metadata.get('archived', {}).pop(file_id, None)
        _save_metadata(metadata)
    except Exception:
        pass

    return jsonify({'success': True, 'message': 'Файл удалён из архива'})


@session_bp.route('/archive/bulk', methods=['DELETE'])
def delete_archived_bulk():
    """Permanently delete multiple files from archive.

    DELETE /api/session/archive/bulk
    Body: { "file_ids": ["id1", "id2", ...] }
    """
    from ..routes.upload import remove_file_record, get_session_store
    storage = get_file_storage()

    params = request.get_json(silent=True) or {}
    file_ids = params.get('file_ids') or []
    deleted = 0

    for file_id in file_ids:
        if file_id not in storage:
            continue
        file_data = storage[file_id]
        if not file_data.get('archived'):
            continue

        # Delete permanent XML file if it exists
        source_path = file_data.get('source_path')
        if source_path:
            try:
                Path(source_path).unlink(missing_ok=True)
            except Exception:
                pass

        # Remove from watcher state if it was watcher-imported
        if source_path and file_data.get('source') == 'watcher':
            watcher = current_app.extensions.get('watcher')
            if watcher is not None and hasattr(watcher, '_state') and watcher._state is not None:
                try:
                    # Find the watcher state key (original watched folder path) by file_id
                    watcher_key = None
                    for key in watcher._state.all_keys():
                        entry = watcher._state.get(key)
                        if entry and str(entry.get('file_id') or '') == file_id:
                            watcher_key = key
                            break
                    if watcher_key:
                        watcher._state.remove(watcher_key)
                except Exception:
                    pass

        remove_file_record(file_id)
        deleted += 1

    # Clean up metadata
    if deleted > 0:
        try:
            metadata = _load_metadata()
            for file_id in file_ids:
                metadata.get('viewed', {}).pop(file_id, None)
                metadata.get('archived', {}).pop(file_id, None)
            _save_metadata(metadata)
        except Exception:
            pass

    return jsonify({'success': True, 'deleted': deleted})


@session_bp.route('/viewed/<file_id>', methods=['POST'])
def mark_viewed(file_id: str):
    """Mark a file as viewed before or during navigation to analysis."""
    from ..routes.upload import mark_file_viewed

    if not mark_file_viewed(file_id):
        return jsonify({
            'success': False,
            'error': 'Файл не найден'
        }), 404

    return jsonify({
        'success': True,
        'viewed': True
    })


@session_bp.route('/file/<file_id>', methods=['GET'])
def get_file_info(file_id: str):
    """
    Get detailed info about a specific file.

    GET /api/session/file/<file_id>
    """
    storage = get_file_storage()

    if file_id not in storage:
        return jsonify({
            'success': False,
            'error': 'Файл не найден'
        }), 404

    data = storage[file_id]
    parsed = data.get('parsed')

    file_info = {
        'file_id': file_id,
        'filename': data['filename'],
        'is_valid': data['is_valid'],
        'validation_errors': data.get('validation_errors', []),
        'parse_error': data.get('parse_error'),
        'analyzed': data['analyzed'],
        'viewed': data.get('viewed', False),
        'created_at': data.get('created_at'),
        'source': data.get('source', 'upload'),
        'source_path': data.get('source_path'),
        'auto_imported': bool(data.get('auto_imported', False)),
    }

    if parsed is not None:
        file_info['info'] = {
            'n_samples': parsed['n_samples'],
            'sampling_rate': parsed['sampling_rate'],
            'duration_sec': round(parsed['n_samples'] / parsed['sampling_rate'], 2),
            'n_leads': 12,
            'lead_names': parsed['lead_names'],
        }
    else:
        file_info['info'] = None

    # Add analysis result if available
    if data['analyzed'] and data['analysis_result']:
        file_info['analysis_result'] = data['analysis_result']

    return jsonify({
        'success': True,
        'file': file_info
    })


def _get_upload_folder() -> Path:
    """Get the resolved upload folder path."""
    return Path(current_app.config.get('UPLOAD_FOLDER_RESOLVED', current_app.config['UPLOAD_FOLDER']))


def _calc_folder_stats(folder: Path) -> dict:
    """Calculate folder statistics: size, file counts."""
    if not folder.exists():
        return {'size_bytes': 0, 'file_count': 0, 'xml_count': 0}
    
    total_size = 0
    file_count = 0
    xml_count = 0
    
    for f in folder.rglob('*'):
        if f.is_file():
            file_count += 1
            try:
                total_size += f.stat().st_size
            except Exception:
                pass
            if f.suffix.lower() == '.xml':
                xml_count += 1
    
    return {
        'size_bytes': total_size,
        'file_count': file_count,
        'xml_count': xml_count,
    }


def _format_bytes(size: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


@session_bp.route('/folder', methods=['GET'])
def get_folder_config():
    """
    Get current upload folder configuration and stats.
    
    GET /api/v1/session/folder
    """
    session_store = get_session_store()
    folder_override = session_store.get_upload_folder_override()
    
    # Determine effective folder
    if folder_override:
        folder = Path(folder_override)
    else:
        folder = _get_upload_folder()
    
    stats = _calc_folder_stats(folder)
    
    return jsonify({
        'success': True,
        'folder': str(folder),
        'folder_override': folder_override,
        'size_bytes': stats['size_bytes'],
        'size_formatted': _format_bytes(stats['size_bytes']),
        'file_count': stats['file_count'],
        'xml_count': stats['xml_count'],
    })


@session_bp.route('/folder', methods=['POST'])
def set_folder_config():
    """
    Set upload folder override.
    
    POST /api/v1/session/folder
    Body: { "folder": "C:/path/to/folder" } or { "folder": null } to reset to default
    """
    params = request.get_json(silent=True) or {}
    folder = params.get('folder')
    
    if folder is not None:
        folder = folder.strip()
        if not folder:
            folder = None
    
    if folder:
        path = Path(folder)
        if not path.exists():
            return jsonify({
                'success': False,
                'error': f'Папка не существует: {folder}'
            }), 400
        if not path.is_dir():
            return jsonify({
                'success': False,
                'error': f'Путь не является папкой: {folder}'
            }), 400
        # Check writability
        try:
            test_file = path / '.write_test'
            test_file.touch()
            test_file.unlink()
        except Exception:
            return jsonify({
                'success': False,
                'error': f'Нет прав на запись в папку: {folder}'
            }), 400
    
    session_store = get_session_store()
    session_store.set_upload_folder_override(folder)
    
    # Update app config for current process
    if folder:
        current_app.config['UPLOAD_FOLDER_RESOLVED'] = folder
    else:
        # Reset to default
        from ..config import resolve_upload_folder
        current_app.config['UPLOAD_FOLDER_RESOLVED'] = str(resolve_upload_folder(current_app.config))
    
    # Ensure new folder exists
    new_folder = Path(current_app.config['UPLOAD_FOLDER_RESOLVED'])
    new_folder.mkdir(parents=True, exist_ok=True)
    
    stats = _calc_folder_stats(new_folder)
    
    return jsonify({
        'success': True,
        'folder': str(new_folder),
        'folder_override': folder,
        'size_bytes': stats['size_bytes'],
        'size_formatted': _format_bytes(stats['size_bytes']),
        'file_count': stats['file_count'],
        'xml_count': stats['xml_count'],
    })


@session_bp.route('/folder/stats', methods=['GET'])
def get_folder_stats():
    """
    Get current upload folder statistics.
    
    GET /api/v1/session/folder/stats
    """
    folder = _get_upload_folder()
    stats = _calc_folder_stats(folder)
    
    return jsonify({
        'success': True,
        'folder': str(folder),
        'size_bytes': stats['size_bytes'],
        'size_formatted': _format_bytes(stats['size_bytes']),
        'file_count': stats['file_count'],
        'xml_count': stats['xml_count'],
    })


@session_bp.route('/folder/clear', methods=['POST'])
def clear_folder():
    """
    Clear all files in the upload folder (XML, cache, metadata, session state).
    
    POST /api/v1/session/folder/clear
    """
    folder = _get_upload_folder()
    
    if not folder.exists():
        return jsonify({
            'success': True,
            'message': 'Папка уже пуста',
            'deleted': 0
        })
    
    deleted_count = 0
    errors = []
    
    # Delete all files in folder
    for f in folder.rglob('*'):
        if f.is_file():
            try:
                f.unlink()
                deleted_count += 1
            except Exception as e:
                errors.append(f'{f.name}: {e}')
    
    # Clear in-memory storage
    storage = get_file_storage()
    with get_storage_lock():
        storage.clear()
    get_session_store().clear_records()
    
    # Recreate folder
    folder.mkdir(parents=True, exist_ok=True)
    
    return jsonify({
        'success': True,
        'message': f'Папка очищена. Удалено файлов: {deleted_count}',
        'deleted': deleted_count,
        'errors': errors
    })


@session_bp.route('/pick-folder', methods=['POST'])
def pick_folder():
    """Open a native folder picker on the server side and return the absolute path.

    The browser-side ``showDirectoryPicker`` API deliberately hides the absolute
    path for security reasons, so we delegate to a Tk dialog running on the
    local Flask process. Tk is bundled with the standard Python distribution on
    Windows; on headless setups we return 501 and the UI falls back to a text
    prompt.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        return jsonify({
            'success': False,
            'error': 'Нативный диалог недоступен (tkinter не установлен). '
                     'Введите путь вручную.',
        }), 501

    result = {'folder': None, 'error': None}

    def _run():
        try:
            root = tk.Tk()
            try:
                root.withdraw()
                try:
                    root.attributes('-topmost', True)
                except Exception:
                    pass
                folder = filedialog.askdirectory(
                    title='Выберите папку для временных файлов',
                    mustexist=True,
                )
                result['folder'] = folder or None
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass
        except Exception as e:
            result['error'] = f'Диалог завершился с ошибкой: {e}'

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout=120)
    if th.is_alive():
        return jsonify({
            'success': False,
            'error': 'Тайм-аут ожидания диалога.',
        }), 504

    if result['error']:
        return jsonify({'success': False, 'error': result['error']}), 500

    return jsonify({'success': True, 'folder': result['folder']})
