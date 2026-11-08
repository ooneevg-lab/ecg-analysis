"""
File upload endpoint.
"""
import uuid
import os
import json
from threading import RLock
from pathlib import Path
from flask import Blueprint, request, jsonify, current_app, session

from ..services.xml_parser import parse_polyspectrum_xml, validate_parsed_data, XMLParseError
from ..services.session_store import SessionStore

upload_bp = Blueprint('upload', __name__)

# In-memory storage for uploaded files (for simplicity)
# In production, use a proper session storage or database
_file_storage = {}
_file_storage_lock = RLock()
_session_store: SessionStore | None = None


def init_session_storage(app) -> SessionStore:
    """Load persisted session records into process-local storage."""
    global _session_store
    upload_folder = app.config.get('UPLOAD_FOLDER_RESOLVED', app.config['UPLOAD_FOLDER'])
    _session_store = SessionStore(upload_folder)
    state = _session_store.load()
    with _file_storage_lock:
        _file_storage.clear()
        _file_storage.update(state.get('records', {}))
    default_threshold = float(app.config.get('DEFAULT_THRESHOLD', 0.35))
    _session_store.set_threshold(_session_store.get_threshold(default_threshold))
    return _session_store


def get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        upload_folder = current_app.config.get('UPLOAD_FOLDER_RESOLVED', current_app.config['UPLOAD_FOLDER'])
        _session_store = SessionStore(upload_folder)
        state = _session_store.load()
        with _file_storage_lock:
            if not _file_storage:
                _file_storage.update(state.get('records', {}))
    return _session_store


def get_storage_lock() -> RLock:
    return _file_storage_lock


def persist_file_record(file_id: str) -> None:
    with _file_storage_lock:
        record = _file_storage.get(file_id)
        if record is None:
            get_session_store().delete_record(file_id)
            return
        get_session_store().save_record(file_id, record)


def persist_all_records() -> None:
    with _file_storage_lock:
        get_session_store().save_records(_file_storage)


def remove_file_record(file_id: str) -> dict | None:
    with _file_storage_lock:
        record = _file_storage.pop(file_id, None)
        get_session_store().delete_record(file_id)
        return record


def current_threshold() -> float:
    return get_session_store().get_threshold(float(current_app.config.get('DEFAULT_THRESHOLD', 0.35)))


def set_current_threshold(threshold: float) -> float:
    return get_session_store().set_threshold(float(threshold))


def _metadata_path() -> Path:
    upload_folder = current_app.config.get('UPLOAD_FOLDER_RESOLVED', current_app.config['UPLOAD_FOLDER'])
    return Path(upload_folder) / '.ecg_metadata.json'


def _load_metadata() -> dict:
    path = _metadata_path()
    if not path.exists():
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_metadata(data: dict) -> None:
    path = _metadata_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def mark_file_viewed(file_id: str) -> bool:
    """Persistently mark an uploaded ECG as viewed.

    Viewing does not archive the record. Archive is an explicit user action.
    """
    with _file_storage_lock:
        record = _file_storage.get(file_id)
        if record is None:
            return False
        record['viewed'] = True
        source_path = record.get('source_path')
    metadata = _load_metadata()
    viewed = metadata.setdefault('viewed', {})
    viewed[file_id] = True
    _save_metadata(metadata)
    if source_path:
        watcher = current_app.extensions.get('watcher')
        if watcher is not None and hasattr(watcher, '_state'):
            try:
                watcher._state.set_flag(str(Path(source_path).resolve()), viewed=True)
            except Exception:
                try:
                    watcher._state.set_flag(source_path, viewed=True)
                except Exception:
                    pass
    persist_file_record(file_id)
    return True


def set_file_archived(file_id: str, archived: bool) -> bool:
    """Toggle the archived flag for a file (used by /api/session/restore)."""
    with _file_storage_lock:
        if file_id not in _file_storage:
            return False
        _file_storage[file_id]['archived'] = bool(archived)
    metadata = _load_metadata()
    archived_map = metadata.setdefault('archived', {})
    if archived:
        archived_map[file_id] = True
    else:
        archived_map.pop(file_id, None)
    _save_metadata(metadata)
    persist_file_record(file_id)
    return True


def get_file_storage():
    """Get the file storage dict."""
    return _file_storage


def load_full_signal(file_id: str) -> dict | None:
    """
    Load full ECG signal on-demand from source XML.
    
    Returns dict with:
        ecg_data: preview data (10s @ 500Hz)
        ecg_data_raw: raw preview data
        ecg_data_full: full resampled signal
        r_peaks: R-peak positions
        sampling_rate: 500 Hz
        ecg_preview_samples: preview length
        ecg_full_samples: full signal length
    
    Returns None if file not found, not valid, or source unavailable.
    """
    storage = get_file_storage()
    file_data = storage.get(file_id)
    if not file_data:
        return None
    if not file_data.get('is_valid'):
        return {"error": "Файл невалиден, сигнал недоступен"}
    
    parsed = file_data.get('parsed')
    source_path = file_data.get('source_path')
    
    # If parsed data is in memory, use it
    if parsed and 'leads' in parsed:
        leads = parsed['leads']
        source_fs = int(parsed.get('sampling_rate', 2000))
    elif source_path:
        # Try to re-parse from XML file
        try:
            from pathlib import Path
            from ..services.xml_parser import parse_polyspectrum_xml, validate_parsed_data
            path = Path(source_path)
            if not path.exists() or not path.is_file():
                return {"error": "Исходный XML-файл не найден. Загрузите файл вручную."}
            parsed = parse_polyspectrum_xml(str(path))
            is_valid, errors = validate_parsed_data(
                parsed,
                min_duration_sec=current_app.config.get('MIN_DURATION_SEC', 10.0)
            )
            if not is_valid:
                return {"error": f"Файл не прошёл валидацию: {', '.join(errors)}"}
            # Update in-memory parsed data
            with get_storage_lock():
                file_data['parsed'] = parsed
                file_data['is_valid'] = is_valid
                file_data['validation_errors'] = errors
                file_data['parse_error'] = None
            leads = parsed['leads']
            source_fs = int(parsed.get('sampling_rate', 2000))
        except Exception as e:
            return {"error": f"Не удалось загрузить сигнал из XML: {e}"}
    else:
        return {"error": "Сигнал недоступен: нет разобранных данных и нет пути к исходному файлу. Загрузите файл вручную."}
    
    # Resample to 500 Hz and prepare signal
    try:
        target_fs = int(current_app.config.get('TARGET_FS', 500))
        model_samples = int(current_app.config.get('MODEL_INPUT_SAMPLES', 5000))
        
        from ..services.preprocessor import ECGPreprocessor
        preprocessor = ECGPreprocessor(target_fs=target_fs, target_samples=model_samples)
        preprocess_result = preprocessor.preprocess(
            leads,
            source_fs=source_fs,
            apply_filter=False,
            apply_notch=False,
        )
        
        if preprocess_result.get('skipped'):
            return {"error": preprocess_result.get('skip_reason', 'Сигнал слишком короткий')}
        
        leads_resampled_full = preprocess_result['leads_full']
        leads_for_display = preprocess_result['leads']
        
        # Get R-peaks from rhythm analysis
        from ..services.rhythm_classifier import RhythmClassifier
        rhythm_classifier = RhythmClassifier(window_samples=int(current_app.config.get('RHYTHM_INPUT_SAMPLES', 10000)))
        rhythm_result = rhythm_classifier.analyze(leads_resampled_full, fs=target_fs)
        r_peaks = rhythm_result.get('r_peaks', [])
        
        signal = {
            "ecg_data": leads_for_display.tolist(),
            "ecg_data_raw": preprocess_result['leads_raw'].tolist(),
            "ecg_data_full": leads_resampled_full.tolist(),
            "ecg_preview_samples": int(leads_for_display.shape[1]),
            "ecg_full_samples": int(leads_resampled_full.shape[1]),
            "r_peaks": r_peaks,
            "sampling_rate": target_fs,
        }
        return signal
    except Exception as e:
        return {"error": f"Ошибка обработки сигнала: {e}"}


def make_storage_entry(
    filename: str,
    temp_path=None,
    *,
    parsed=None,
    is_valid: bool = False,
    errors=None,
    parse_error: str | None = None,
    created_at: str | None = None,
    source: str = 'upload',
    source_path: str | None = None,
    auto_imported: bool = False,
) -> dict:
    """Build a new _file_storage record with consistent defaults."""
    return {
        'filename': filename,
        'parsed': parsed,
        'temp_path': str(temp_path) if temp_path else None,
        'is_valid': is_valid,
        'validation_errors': list(errors) if errors else [],
        'parse_error': parse_error,
        'analyzed': False,
        'viewed': False,
        'archived': False,
        'analysis_result': None,
        'created_at': created_at or _now_iso(),
        'source': source,
        'source_path': source_path,
        'auto_imported': auto_imported,
    }


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec='seconds')


def _uploaded_file_created_at() -> str | None:
    raw = request.form.get('last_modified_ms')
    if raw is None:
        return None
    try:
        from datetime import datetime
        value = float(raw)
        if value <= 0:
            return None
        return datetime.fromtimestamp(value / 1000.0).isoformat(timespec='seconds')
    except (TypeError, ValueError, OSError):
        return None


@upload_bp.route('/ecg', methods=['POST'])
def upload_ecg():
    """
    Upload ECG XML file.

    POST /api/upload/ecg

    Form data:
        file: XML file

    Returns:
        JSON with file_id and basic info, or error
    """
    if 'file' not in request.files:
        return jsonify({
            'success': False,
            'error': 'Файл не выбран'
        }), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({
            'success': False,
            'error': 'Файл не выбран'
        }), 400

    if not file.filename.lower().endswith('.xml'):
        return jsonify({
            'success': False,
            'error': 'Неверный формат файла. Ожидается XML.'
        }), 400

    # Generate unique file ID
    file_id = str(uuid.uuid4())
    created_at = _uploaded_file_created_at()

    # Save file permanently for later full signal loading
    upload_folder = Path(current_app.config.get('UPLOAD_FOLDER_RESOLVED', current_app.config['UPLOAD_FOLDER']))
    upload_folder.mkdir(parents=True, exist_ok=True)
    permanent_path = upload_folder / f"{file_id}.xml"

    try:
        file.save(str(permanent_path))

        # Parse XML
        parsed = parse_polyspectrum_xml(str(permanent_path))

        # Validate
        is_valid, errors = validate_parsed_data(
            parsed,
            min_duration_sec=current_app.config.get('MIN_DURATION_SEC', 10.0)
        )

        source_path = str(permanent_path)

        # Store parsed data
        with _file_storage_lock:
            _file_storage[file_id] = make_storage_entry(
                file.filename,
                None,
                parsed=parsed,
                is_valid=is_valid,
                errors=errors,
                created_at=created_at,
                source_path=source_path,
            )
        persist_file_record(file_id)

        # Calculate duration
        duration_sec = parsed['n_samples'] / parsed['sampling_rate']

        return jsonify({
            'success': True,
            'file_id': file_id,
            'filename': file.filename,
            'info': {
                'n_samples': parsed['n_samples'],
                'sampling_rate': parsed['sampling_rate'],
                'duration_sec': round(duration_sec, 2),
                'n_leads': 12,
                'lead_names': parsed['lead_names'],
                'has_qrs_points': parsed.get('qrs_points') is not None
            },
            'is_valid': is_valid,
            'validation_errors': errors
        })

    except XMLParseError as e:
        source_path = str(permanent_path)
        with _file_storage_lock:
            _file_storage[file_id] = make_storage_entry(
                file.filename, None,
                is_valid=False, errors=[str(e)], parse_error=str(e),
                created_at=created_at,
                source_path=source_path,
            )
        persist_file_record(file_id)
        return jsonify({
            'success': True,
            'file_id': file_id,
            'filename': file.filename,
            'is_valid': False,
            'validation_errors': [str(e)],
            'parse_error': str(e)
        })

    except Exception as e:
        source_path = str(permanent_path)
        import traceback
        error_msg = f'Ошибка обработки файла: {str(e)}'
        with _file_storage_lock:
            _file_storage[file_id] = make_storage_entry(
                file.filename, None,
                is_valid=False, errors=[error_msg], parse_error=error_msg,
                created_at=created_at,
                source_path=source_path,
            )
        persist_file_record(file_id)
        return jsonify({
            'success': True,
            'file_id': file_id,
            'filename': file.filename,
            'is_valid': False,
            'validation_errors': [error_msg],
            'parse_error': error_msg
        })


@upload_bp.route('/ecg/<file_id>', methods=['DELETE'])
def delete_file(file_id: str):
    """
    Delete uploaded file.

    DELETE /api/upload/ecg/<file_id>
    """
    with _file_storage_lock:
        exists = file_id in _file_storage
    if not exists:
        return jsonify({
            'success': False,
            'error': 'Файл не найден'
        }), 404

    file_data = remove_file_record(file_id)

    # Delete permanent XML file if it exists
    source_path = file_data.get('source_path') if file_data else None
    if source_path:
        try:
            Path(source_path).unlink(missing_ok=True)
        except Exception:
            pass

    # Only update watcher state for watcher-imported files (not manual uploads)
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
                    source = Path(watcher_key)
                    if source.exists() and source.is_file():
                        stat = source.stat()
                        watcher._state.upsert(watcher_key, {
                            'file_id': file_id,
                            'mtime': stat.st_mtime,
                            'size': stat.st_size,
                            'viewed': bool(file_data.get('viewed')),
                            'analyzed': True,
                            'deleted_from_session': True,
                        })
                    else:
                        watcher._state.remove(watcher_key)
            except Exception:
                pass

    return jsonify({
        'success': True,
        'message': 'Файл удален'
    })
