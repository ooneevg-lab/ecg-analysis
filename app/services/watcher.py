"""
Watched-folder importer for the cardiograph export pipeline.

ECGFolderWatcher runs on a daemon thread (started in app.create_app()).
It uses watchdog to react to file-system events, and a small worker
thread to debounce / stability-check files, parse them, and register
them with the in-memory _file_storage.

Design notes
------------
* The watcher does NOT delete the original file from the watched folder
  unless ``WATCHED_DELETE_AFTER_IMPORT = True``. The cardiograph keeps
  its own audit log there and we should not surprise the user.
* On import we copy the XML to a temp file, run the existing
  ``parse_polyspectrum_xml`` / ``validate_parsed_data`` pipeline, then
  delete the temp copy (Feature 1: parsed data lives in memory).
* Persistence is delegated to ``watcher_state.WatcherState``, a small
  JSON file storing {path -> {file_id, mtime, size, viewed, analyzed}}.
  On restart the state lets us avoid re-importing unchanged files while
  still restoring the "viewed" / "analyzed" UI state.
* The watcher re-uses ``analysis.run_analysis_for_file`` for auto-analyze,
  so the auto pipeline is identical to the manual one.
* Audit log (JSONL) records each import attempt with outcome for traceability.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .watcher_config import WatcherConfig
from .watcher_state import WatcherState, now_iso

if TYPE_CHECKING:
    from flask import Flask

log = logging.getLogger(__name__)

_AUDIT_FILENAME = '.watcher_audit.jsonl'


class WatcherAuditLog:
    """Thread-safe append-only JSONL audit log for watcher imports."""

    def __init__(self, upload_folder: str | os.PathLike):
        self._path = Path(upload_folder) / _AUDIT_FILENAME
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: dict) -> None:
        """Append a single audit entry (will be serialized to JSONL)."""
        with self._lock:
            try:
                with open(self._path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            except Exception as e:
                log.warning('Failed to write watcher audit log: %s', e)

    def read_all(self, limit: int | None = None) -> list[dict]:
        """Read all audit entries (newest first if limit specified)."""
        with self._lock:
            if not self._path.exists():
                return []
            try:
                entries = []
                with open(self._path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
                if limit:
                    return entries[-limit:][::-1]  # newest first
                return entries[::-1]  # newest first
            except Exception as e:
                log.warning('Failed to read watcher audit log: %s', e)
                return []

    def read_recent(self, limit: int = 100) -> list[dict]:
        """Read the most recent audit entries."""
        return self.read_all(limit=limit)

# Make watchdog optional so the rest of the app still runs in environments
# where it can't be installed (e.g. locked-down Python builds).
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except Exception as _e:  # pragma: no cover
    WATCHDOG_AVAILABLE = False
    FileSystemEventHandler = None  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment,misc]
    log.warning('watchdog is not available: %s', _e)


class _XMLHandler(FileSystemEventHandler if WATCHDOG_AVAILABLE else object):
    """Filter watchdog events down to ``*.xml`` creations and moves."""

    def __init__(self, watcher: 'ECGFolderWatcher'):
        self._watcher = watcher

    def on_created(self, event):
        if getattr(event, 'is_directory', False):
            return
        self._maybe_enqueue(event.src_path)

    def on_moved(self, event):
        if getattr(event, 'is_directory', False):
            return
        self._maybe_enqueue(event.dest_path)

    def _maybe_enqueue(self, raw_path: str) -> None:
        try:
            path = Path(raw_path)
        except (TypeError, ValueError):
            return
        if path.suffix.lower() != '.xml':
            return
        self._watcher._enqueue(path)


class ECGFolderWatcher:
    """Long-lived folder observer for ECG XML files."""

    def __init__(self, app: 'Flask'):
        self.app = app
        self.folder: str | None = app.config.get('WATCHED_FOLDER')
        self.stable_sec: float = float(app.config.get('WATCHED_STABLE_SEC', 2.0))
        self.auto_analyze: bool = bool(app.config.get('WATCHED_AUTOMATIC_ANALYSIS', False))
        self.delete_after: bool = bool(app.config.get('WATCHED_DELETE_AFTER_IMPORT', False))

        # Persisted config (overrides env-vars if present). Lets the user pick a
        # folder via the UI modal and have it survive a server restart.
        upload_folder = app.config.get('UPLOAD_FOLDER_RESOLVED', app.config['UPLOAD_FOLDER'])
        self._config_store = WatcherConfig(upload_folder)
        persisted = self._config_store.load()
        if persisted:
            if 'folder' in persisted:
                self.folder = persisted.get('folder') or None
                self.app.config['WATCHED_FOLDER'] = self.folder
            if 'auto_analyze' in persisted:
                self.auto_analyze = bool(persisted.get('auto_analyze'))
                self.app.config['WATCHED_AUTOMATIC_ANALYSIS'] = self.auto_analyze
            if 'delete_after_import' in persisted:
                self.delete_after = bool(persisted.get('delete_after_import'))
                self.app.config['WATCHED_DELETE_AFTER_IMPORT'] = self.delete_after
            if 'stable_sec' in persisted:
                try:
                    self.stable_sec = float(persisted.get('stable_sec') or self.stable_sec)
                except (TypeError, ValueError):
                    pass

        self._observer: Observer | None = None
        self._queue: list[Path] = []
        self._queue_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._folder_generation: int = 0  # Incremented on folder change to invalidate stale queue items
        self._state = WatcherState(upload_folder)
        self._audit_log = WatcherAuditLog(upload_folder)
        self._import_lock = threading.Lock()  # Protects entire import operation
        self._imported_count = 0
        self._last_error: str | None = None
        self._paused: bool = bool(persisted.get('paused'))
        self._state.load()

    # --- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._observer is not None:
            return
        if self._paused:
            log.info('ECGFolderWatcher: paused — start suppressed')
            return
        if not self.folder:
            log.info('ECGFolderWatcher: WATCHED_FOLDER is not set; watcher stays idle')
            return
        if not WATCHDOG_AVAILABLE:
            self._last_error = 'Библиотека watchdog не установлена (pip install watchdog)'
            log.warning('ECGFolderWatcher: %s', self._last_error)
            return
        folder = Path(self.folder)
        if not folder.is_dir():
            self._last_error = f'Папка не найдена: {folder}'
            log.warning('ECGFolderWatcher: %s', self._last_error)
            return

        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True, name='ecg-watcher-worker'
        )
        self._worker.start()

        handler = _XMLHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(folder), recursive=False)
        self._observer.start()
        log.info('ECGFolderWatcher: watching %s', folder)

        # Drain whatever is already in the folder.
        self.rescan()

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception as e:
                log.warning('ECGFolderWatcher: observer stop failed: %s', e)
            self._observer = None
        self._stop_event.set()
        if self._worker is not None:
            self._worker.join(timeout=2)
            self._worker = None
        log.info('ECGFolderWatcher: stopped')

    def update_config(
        self,
        folder: str | None = None,
        auto_analyze: bool | None = None,
        delete_after: bool | None = None,
    ) -> None:
        old_folder = self.folder
        if folder is not None:
            self.folder = folder or None
            self.app.config['WATCHED_FOLDER'] = self.folder
        if auto_analyze is not None:
            self.auto_analyze = bool(auto_analyze)
            self.app.config['WATCHED_AUTOMATIC_ANALYSIS'] = self.auto_analyze
        if delete_after is not None:
            self.delete_after = bool(delete_after)
            self.app.config['WATCHED_DELETE_AFTER_IMPORT'] = self.delete_after
        self._persist_config()

        # When the watched folder changes, purge stale watcher data so the UI
        # doesn't keep showing files from the old folder and the new folder's
        # files get imported fresh.
        folder_changed = folder is not None and folder != old_folder
        if folder_changed:
            # Increment generation to invalidate any stale queue items
            self._folder_generation += 1
            current_generation = self._folder_generation
            with self._queue_lock:
                self._queue.clear()
            self._state._data = {'imported': {}}
            self._state._flush_unlocked()
            self._imported_count = 0
            self._last_error = None
            with self.app.app_context():
                from ..routes.upload import get_file_storage, get_storage_lock, persist_file_record
                storage = get_file_storage()
                with get_storage_lock():
                    to_delete = [
                        fid for fid, rec in storage.items()
                        if rec.get('source') == 'watcher'
                    ]
                    for fid in to_delete:
                        del storage[fid]
                        persist_file_record(fid)

        self.stop()

        # Clear the queue again after stop — the old observer may have fired
        # remaining events into the queue during the stop() call.
        if folder_changed:
            with self._queue_lock:
                self._queue.clear()

        self.start()

    def pause(self) -> None:
        self.stop()
        self._paused = True
        self._persist_config()

    def resume(self) -> None:
        self._paused = False
        self._persist_config()
        self.start()

    def rescan(self) -> int:
        if not self.folder:
            return 0
        folder = Path(self.folder)
        if not folder.is_dir():
            return 0
        count = 0
        for path in sorted(folder.glob('*.xml')):
            try:
                st = path.stat()
            except OSError:
                continue
            key = str(path.resolve())
            prev = self._state.get(key)
            # Files deleted from the session — never re-import.
            if prev and prev.get('deleted_from_session'):
                continue
            # Skip files that are already known AND analyzed with the same
            # (mtime, size) — no work to do, no need to drag them through
            # the queue just to early-return them in _import_file. New or
            # changed files still go through the normal enqueue path.
            if (
                prev
                and prev.get('mtime') == st.st_mtime
                and prev.get('size') == st.st_size
                and prev.get('analyzed')
            ):
                continue
            self._enqueue(path)
            count += 1
        return count

    # --- status ----------------------------------------------------------

    def get_status(self) -> dict:
        return {
            'folder': self.folder,
            'running': self._observer is not None,
            'auto_analyze': self.auto_analyze,
            'delete_after_import': self.delete_after,
            'stable_sec': self.stable_sec,
            'imported_count': self._imported_count,
            'queue_size': len(self._queue),
            'last_error': self._last_error,
        }

    def _persist_config(self) -> None:
        try:
            self._config_store.save({
                'folder': self.folder,
                'auto_analyze': self.auto_analyze,
                'delete_after_import': self.delete_after,
                'stable_sec': self.stable_sec,
                'paused': self._paused,
            })
        except Exception as e:
            log.warning('ECGFolderWatcher: failed to persist config: %s', e)

    # --- internals -------------------------------------------------------

    def _enqueue(self, path: Path) -> None:
        with self._queue_lock:
            # Avoid duplicates of the same path sitting in the queue.
            sp = str(path)
            if any(str(p[0]) == sp for p in self._queue):
                return
            self._queue.append((path, self._folder_generation))
        log.info('ECGFolderWatcher: enqueued %s', path.name)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._queue_lock:
                item = self._queue.pop(0) if self._queue else None
            if item is None:
                self._stop_event.wait(0.5)
                continue
            path, generation = item
            # Discard stale items from previous folder generations
            if generation != self._folder_generation:
                log.debug('ECGFolderWatcher: discarding stale item from generation %d (current %d): %s',
                          generation, self._folder_generation, path.name)
                continue
            try:
                if not self._wait_stable(path):
                    continue
                self._import_file(path)
            except Exception as e:
                self._last_error = f'{path}: {e}'
                log.exception('ECGFolderWatcher: import failed for %s', path)

    def _wait_stable(self, path: Path) -> bool:
        """Block until the file size hasn't changed for ``stable_sec`` seconds.

        Returns True if the file is stable, False if it disappeared.
        """
        if not path.exists():
            return False
        last_size = -1
        stable_since: float | None = None
        deadline_start = time.time()
        while not self._stop_event.is_set():
            try:
                size = path.stat().st_size
            except OSError:
                return False
            if size == last_size:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= self.stable_sec:
                    return True
            else:
                stable_since = None
                last_size = size
            # Give up on files that grow forever (>5 minutes).
            if time.time() - deadline_start > 300:
                log.warning('ECGFolderWatcher: giving up on %s (still growing)', path)
                return False
            self._stop_event.wait(0.5)
        return False

    def _import_file(self, path: Path) -> None:
        """Import a single ECG file with full locking to prevent race conditions."""
        if not path.exists():
            return

        # Use a dedicated import lock to prevent concurrent imports of the same file
        # and race conditions with HTTP routes modifying storage.
        with self._import_lock:
            try:
                stat = path.stat()
            except OSError:
                return

            key = str(path.resolve())
            prev = self._state.get(key)
            mtime = stat.st_mtime
            size = stat.st_size
            file_id = (prev or {}).get('file_id') or str(uuid.uuid4())

            # File was explicitly deleted from the session by the user — never
            # re-import it, even if it changes on disk.
            if prev and prev.get('deleted_from_session'):
                return

            # Already imported and unchanged → just make sure storage knows about it.
            # Files that were previously analyzed are left as placeholders; files
            # that were seen but never analyzed (e.g. auto_analyze was off) fall
            # through to the re-import path below so they get a chance to analyze.
            if prev and prev.get('mtime') == mtime and prev.get('size') == size and prev.get('analyzed'):
                self._restore_storage_entry(prev, path)
                self._audit_log.append({
                    'timestamp': now_iso(),
                    'event': 'skipped_unchanged',
                    'file_id': file_id,
                    'filename': path.name,
                    'source_path': key,
                    'mtime': mtime,
                    'size': size,
                    'reason': 'already_imported_analyzed',
                })
                return

            # New file or changed on disk → re-import.
            tmp_path: Path | None = None
            permanent_path: Path | None = None
            audit_entry = {
                'timestamp': now_iso(),
                'event': 'import_started',
                'file_id': file_id,
                'filename': path.name,
                'source_path': key,
                'mtime': mtime,
                'size': size,
            }
            try:
                fd, tmp_name = tempfile.mkstemp(suffix='.xml', prefix='ecg_import_')
                os.close(fd)
                tmp_path = Path(tmp_name)
                shutil.copy2(str(path), str(tmp_path))
                parsed, is_valid, errors, parse_error = self._parse_copy(tmp_path)

                # Copy to permanent storage location (same as manual upload)
                if is_valid:
                    upload_folder = Path(self.app.config.get('UPLOAD_FOLDER_RESOLVED', self.app.config['UPLOAD_FOLDER']))
                    upload_folder.mkdir(parents=True, exist_ok=True)
                    permanent_path = upload_folder / f"{file_id}.xml"
                    shutil.copy2(str(path), str(permanent_path))

            except Exception as e:
                self._last_error = f'{path}: {e}'
                log.exception('ECGFolderWatcher: copy/parse failed for %s', path)
                audit_entry.update({
                    'event': 'import_failed',
                    'error': str(e),
                    'stage': 'copy_parse',
                })
                self._audit_log.append(audit_entry)
                return
            finally:
                if tmp_path is not None:
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except OSError:
                        pass

            with self.app.app_context():
                from ..routes.upload import (
                    current_threshold,
                    get_file_storage,
                    get_storage_lock,
                    make_storage_entry,
                    persist_file_record,
                )
                from ..routes.analysis import run_analysis_for_file
                storage = get_file_storage()
                # Use permanent path for source_path (like manual upload)
                storage_source_path = str(permanent_path) if permanent_path else key
                entry = make_storage_entry(
                    path.name, None,
                    parsed=parsed, is_valid=is_valid, errors=errors,
                    parse_error=parse_error,
                    created_at=datetime.fromtimestamp(mtime).isoformat(timespec='seconds'),
                    source='watcher', source_path=storage_source_path, auto_imported=True,
                )
                # Preserve "viewed" from previous state (e.g. across restarts).
                if prev and prev.get('viewed'):
                    entry['viewed'] = True
                with get_storage_lock():
                    storage[file_id] = entry

                # Auto-analyze (inside app context)
                analysis_success = False
                used_threshold = None
                analysis_result = None
                if self.auto_analyze and is_valid and parsed is not None:
                    try:
                        used_threshold = current_threshold()
                        analysis_success, analysis_result, _, _ = run_analysis_for_file(
                            file_id, include_xai=False, client_threshold=used_threshold
                        )
                    except Exception as e:
                        log.exception('ECGFolderWatcher: auto-analyze failed for %s', file_id)
                persist_file_record(file_id)

        # Persist state
        analyzed_now = bool(prev and prev.get('analyzed'))
        final_analyzed = analyzed_now or analysis_success
        state_entry = {
            'file_id': file_id,
            'mtime': mtime,
            'size': size,
            'imported_at': now_iso(),
            'viewed': bool(prev and prev.get('viewed')),
            'analyzed': final_analyzed,
            'threshold': used_threshold,
        }
        if permanent_path:
            state_entry['permanent_path'] = str(permanent_path)
        self._state.upsert(key, state_entry)
        self._imported_count += 1
        log.info('ECGFolderWatcher: imported %s (file_id=%s)', path.name, file_id)

        # Audit log: import completed
        audit_entry.update({
            'event': 'import_completed',
            'is_valid': is_valid,
            'validation_errors': errors,
            'parse_error': parse_error,
            'analyzed': final_analyzed,
            'auto_analyze_enabled': self.auto_analyze,
            'threshold': used_threshold,
        })
        if analysis_result:
            audit_entry.update({
                'verdict': analysis_result.get('verdict'),
                'pred_class': analysis_result.get('pred_class'),
                'probability': analysis_result.get('probability'),
                'rhythm_class': analysis_result.get('rhythm_class'),
                'hr_bpm': analysis_result.get('hr_bpm'),
            })
        self._audit_log.append(audit_entry)

        # Optional: delete original from watched folder.
        if self.delete_after:
            try:
                path.unlink()
                self._audit_log.append({
                    'timestamp': now_iso(),
                    'event': 'original_deleted',
                    'file_id': file_id,
                    'source_path': key,
                })
            except OSError as e:
                log.warning('ECGFolderWatcher: could not delete %s: %s', path, e)

    def _parse_copy(self, tmp_path: Path):
        """Run the standard XML pipeline on a temp copy.

        Returns (parsed, is_valid, errors, parse_error).
        ``parsed`` is None if the file failed to parse.
        """
        from ..services.xml_parser import (
            XMLParseError, parse_polyspectrum_xml, validate_parsed_data,
        )
        try:
            parsed = parse_polyspectrum_xml(str(tmp_path))
        except XMLParseError as e:
            return None, False, [str(e)], str(e)
        except Exception as e:
            msg = f'Ошибка обработки файла: {e}'
            return None, False, [msg], msg

        is_valid, errors = validate_parsed_data(
            parsed,
            min_duration_sec=float(self.app.config.get('MIN_DURATION_SEC', 10.0)),
        )
        return parsed, is_valid, errors, None

    def _restore_storage_entry(self, prev: dict, path: Path) -> None:
        """Re-create a _file_storage entry after a server restart.

        If the file was previously analyzed, try to restore the full analysis
        result from the session store. Otherwise, create a placeholder that
        prompts the user to re-analyze.
        """
        file_id = prev.get('file_id')
        if not file_id:
            return
        if prev.get('deleted_from_session'):
            return
        with self.app.app_context():
            from ..routes.upload import get_file_storage, get_session_store, get_storage_lock
            storage = get_file_storage()
            session_store = get_session_store()
            prev_viewed = bool(prev.get('viewed'))
            prev_analyzed = bool(prev.get('analyzed'))
            # Archive is manual-only; watcher restores active placeholders.
            prev_archived = False
            if file_id in storage:
                # Already in memory — sync the viewed flag and archive state.
                with get_storage_lock():
                    if prev_viewed:
                        storage[file_id]['viewed'] = True
                return

            # Try to restore full analysis result from session store
            analysis_result = None
            if prev_analyzed:
                analysis_result = session_store.get_analysis_result(file_id)

            # Use permanent_path from state if available, else fall back to original
            permanent_path = prev.get('permanent_path')
            source_path = permanent_path if permanent_path else str(path.resolve())

            with get_storage_lock():
                storage[file_id] = {
                    'filename': path.name,
                    'parsed': None,
                    'temp_path': None,
                    'is_valid': False,
                    'validation_errors': [],
                    'parse_error': None,
                    'analyzed': bool(analysis_result),
                    'viewed': prev_viewed,
                    'archived': prev_archived,
                    'analysis_result': analysis_result,
                    'created_at': datetime.fromtimestamp(
                        float(prev.get('mtime', 0))
                    ).isoformat(timespec='seconds'),
                    'source': 'watcher',
                    'source_path': source_path,
                    'auto_imported': True,
                }
                if not analysis_result:
                    storage[file_id]['validation_errors'] = [
                        'Запись была импортирована ранее — откройте для повторного анализа'
                    ]


def init_watcher(app: 'Flask') -> ECGFolderWatcher | None:
    """Build and start a watcher from a Flask app. Returns None if disabled."""
    watcher = ECGFolderWatcher(app)
    if not app.config.get('WATCHED_FOLDER'):
        return watcher  # Idle; UI can still call update_config later.
    watcher.start()
    return watcher
