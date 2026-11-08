"""
Persistent state for the watched-folder importer.

Stored as a small JSON file next to .ecg_metadata.json inside UPLOAD_FOLDER.
Keeps a record per imported file so that on restart we can:
  * skip re-importing files whose (mtime, size) is unchanged;
  * restore "viewed" and "analyzed" flags for files whose _file_storage entry
    was wiped by an in-memory restart.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)

_STATE_FILENAME = '.watched_state.json'


class WatcherState:
    """Thread-safe wrapper around the JSON state file."""

    def __init__(self, upload_folder: str | os.PathLike):
        self._path = Path(upload_folder) / _STATE_FILENAME
        self._lock = Lock()
        self._data: dict[str, Any] = {'imported': {}}
        self._loaded = False

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._data = {'imported': {}}
                self._loaded = True
                return
            try:
                with open(self._path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                if not isinstance(raw, dict) or 'imported' not in raw:
                    raw = {'imported': {}}
                self._data = raw
            except Exception as e:
                log.exception('Failed to load watcher state: %s', e)
                self._data = {'imported': {}}
            self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def get(self, key: str) -> dict[str, Any] | None:
        self._ensure_loaded()
        with self._lock:
            entry = self._data.get('imported', {}).get(key)
            return dict(entry) if entry else None

    def upsert(self, key: str, entry: dict[str, Any]) -> None:
        self._ensure_loaded()
        with self._lock:
            self._data.setdefault('imported', {})[key] = dict(entry)
            self._flush_unlocked()

    def set_flag(self, key: str, **flags: Any) -> None:
        self._ensure_loaded()
        with self._lock:
            entry = self._data.setdefault('imported', {}).setdefault(key, {})
            entry.update(flags)
            self._flush_unlocked()

    def remove(self, key: str) -> None:
        self._ensure_loaded()
        with self._lock:
            self._data.setdefault('imported', {}).pop(key, None)
            self._flush_unlocked()

    def all_keys(self) -> list[str]:
        self._ensure_loaded()
        with self._lock:
            return list(self._data.get('imported', {}).keys())

    def _flush_unlocked(self) -> None:
        """Write the JSON file atomically. Caller must hold the lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix='.watched_state.', suffix='.json', dir=str(self._path.parent)
            )
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            log.exception('Failed to persist watcher state: %s', e)


def now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')
