"""
HTTP endpoints for the watched-folder importer.

GET  /api/watcher/status   — current folder, running/stopped, counters.
POST /api/watcher/config   — change folder / auto-analyze / delete-after.
POST /api/watcher/pick-folder — open a native folder picker, return abs path.
POST /api/watcher/rescan   — re-enqueue everything currently in the folder.
POST /api/watcher/pause    — stop the observer (keep state).
POST /api/watcher/resume   — start the observer again.
POST /api/watcher/clear    — wipe the persisted state (next rescan re-imports).
"""
from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

watcher_bp = Blueprint('watcher', __name__)


def _get_watcher():
    return current_app.extensions.get('watcher')


@watcher_bp.route('/status', methods=['GET'])
def watcher_status():
    w = _get_watcher()
    if w is None:
        return jsonify({
            'success': False,
            'error': 'Watcher не инициализирован',
        }), 503
    from ..routes.upload import current_threshold
    return jsonify({
        'success': True,
        'status': {
            **w.get_status(),
            'threshold': current_threshold(),
        },
    })


@watcher_bp.route('/config', methods=['POST'])
def watcher_config():
    w = _get_watcher()
    if w is None:
        return jsonify({'success': False, 'error': 'Watcher не инициализирован'}), 503

    params = request.get_json(silent=True) or {}
    folder = params.get('folder')
    auto_analyze = params.get('auto_analyze')
    delete_after = params.get('delete_after_import')

    if folder is not None and not isinstance(folder, str):
        return jsonify({'success': False, 'error': 'folder должен быть строкой'}), 400
    if folder is not None:
        folder = folder.strip() or None

    # Validate folder existence before restarting.
    if folder:
        from pathlib import Path
        if not Path(folder).is_dir():
            return jsonify({
                'success': False,
                'error': f'Папка не найдена: {folder}',
            }), 400

    w.update_config(folder=folder, auto_analyze=auto_analyze, delete_after=delete_after)
    return jsonify({'success': True, 'status': w.get_status()})


@watcher_bp.route('/rescan', methods=['POST'])
def watcher_rescan():
    w = _get_watcher()
    if w is None:
        return jsonify({'success': False, 'error': 'Watcher не инициализирован'}), 503
    w.rescan()
    return jsonify({'success': True, 'status': w.get_status()})


@watcher_bp.route('/pause', methods=['POST'])
def watcher_pause():
    w = _get_watcher()
    if w is None:
        return jsonify({'success': False, 'error': 'Watcher не инициализирован'}), 503
    w.pause()
    return jsonify({'success': True, 'status': w.get_status()})


@watcher_bp.route('/resume', methods=['POST'])
def watcher_resume():
    w = _get_watcher()
    if w is None:
        return jsonify({'success': False, 'error': 'Watcher не инициализирован'}), 503
    w.resume()
    return jsonify({'success': True, 'status': w.get_status()})


@watcher_bp.route('/clear', methods=['POST'])
def watcher_clear():
    """Wipe persisted state. Next rescan will re-import everything."""
    w = _get_watcher()
    if w is None:
        return jsonify({'success': False, 'error': 'Watcher не инициализирован'}), 503
    state = w._state
    state._data = {'imported': {}}
    state._flush_unlocked()
    w._imported_count = 0
    w._last_error = None
    w.rescan()
    return jsonify({'success': True, 'status': w.get_status()})


@watcher_bp.route('/pick-folder', methods=['POST'])
def watcher_pick_folder():
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
    except Exception as e:  # pragma: no cover
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
                # Lift above other windows so the dialog is visible.
                try:
                    root.attributes('-topmost', True)
                except Exception:
                    pass
                folder = filedialog.askdirectory(
                    title='Выберите папку экспорта кардиографа',
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
            'error': 'Диалог не завершён (таймаут 120 сек)',
        }), 504

    if result['error']:
        return jsonify({'success': False, 'error': result['error']}), 500

    folder = result['folder']
    if not folder:
        # User cancelled the dialog.
        return jsonify({'success': True, 'folder': None, 'cancelled': True})

    folder = os.path.normpath(folder)
    return jsonify({'success': True, 'folder': folder})


@watcher_bp.route('/audit', methods=['GET'])
def watcher_audit():
    """Get the watcher import audit log.

    Query params:
        limit: max number of entries to return (default 100, max 1000)

    Returns:
        JSON with list of audit entries (newest first).
    """
    w = _get_watcher()
    if w is None:
        return jsonify({'success': False, 'error': 'Watcher не инициализирован'}), 503

    params = request.args
    try:
        limit = int(params.get('limit', 100))
        limit = min(max(limit, 1), 1000)
    except (TypeError, ValueError):
        limit = 100

    if not hasattr(w, '_audit_log'):
        return jsonify({'success': True, 'entries': []})

    entries = w._audit_log.read_recent(limit=limit)
    return jsonify({'success': True, 'entries': entries, 'count': len(entries)})


@watcher_bp.route('/audit/open', methods=['POST'])
def watcher_audit_open():
    """Restore/open a watcher audit entry using the original XML path."""
    w = _get_watcher()
    if w is None:
        return jsonify({'success': False, 'error': 'Watcher не инициализирован'}), 503

    params = request.get_json(silent=True) or {}
    file_id = str(params.get('file_id') or '').strip()
    if not file_id:
        return jsonify({'success': False, 'error': 'Не указан file_id'}), 400

    from ..routes.upload import (
        get_file_storage,
        get_session_store,
        get_storage_lock,
        make_storage_entry,
        persist_file_record,
    )

    storage = get_file_storage()
    with get_storage_lock():
        existing = storage.get(file_id)
        if existing and existing.get('analysis_result'):
            return jsonify({'success': True, 'file_id': file_id})

    audit_entry = None
    if hasattr(w, '_audit_log'):
        for entry in w._audit_log.read_all():
            if str(entry.get('file_id') or '') == file_id:
                audit_entry = entry
                break

    # Try to get permanent_path from watcher state first
    state_entry = None
    source_path = None
    if hasattr(w, '_state'):
        for key in w._state.all_keys():
            candidate = w._state.get(key)
            if candidate and str(candidate.get('file_id') or '') == file_id:
                state_entry = candidate
                source_path = candidate.get('permanent_path') or key
                break

    # Fallback to audit entry source_path if state not found
    if not source_path:
        source_path = (audit_entry or {}).get('source_path')

    if not source_path:
        return jsonify({
            'success': False,
            'error': 'В журнале нет пути к исходному XML для этой записи',
        }), 404

    path = Path(source_path)
    if not path.is_file():
        return jsonify({
            'success': False,
            'error': 'Исходный XML не найден. Файл был удалён или перемещён.',
        }), 404

    try:
        from ..services.xml_parser import parse_polyspectrum_xml, validate_parsed_data

        parsed = parse_polyspectrum_xml(str(path))
        is_valid, errors = validate_parsed_data(
            parsed,
            min_duration_sec=current_app.config.get('MIN_DURATION_SEC', 10.0),
        )
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Не удалось открыть XML из журнала: {e}',
        }), 400

    analysis_result = get_session_store().get_analysis_result(file_id)
    created_at = None
    try:
        created_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds')
    except OSError:
        pass
    if audit_entry and audit_entry.get('timestamp'):
        created_at = audit_entry.get('timestamp')

    with get_storage_lock():
        record = storage.get(file_id)
        if record is None:
            record = make_storage_entry(
                path.name,
                None,
                parsed=parsed,
                is_valid=is_valid,
                errors=errors,
                created_at=created_at,
                source='watcher',
                source_path=str(path.resolve()),
                auto_imported=True,
            )
            if state_entry and state_entry.get('viewed'):
                record['viewed'] = True
            if analysis_result:
                record['analysis_result'] = analysis_result
                record['analyzed'] = True
            storage[file_id] = record
        else:
            record['parsed'] = parsed
            record['is_valid'] = is_valid
            record['validation_errors'] = errors
            record['parse_error'] = None
            record['source_path'] = str(path.resolve())
            if analysis_result and not record.get('analysis_result'):
                record['analysis_result'] = analysis_result
                record['analyzed'] = True
    persist_file_record(file_id)

    if not is_valid:
        return jsonify({
            'success': False,
            'error': '; '.join(errors) or 'Файл не прошёл валидацию',
        }), 400

    return jsonify({'success': True, 'file_id': file_id})
