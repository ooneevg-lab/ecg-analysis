"""
Persistent configuration for the watched-folder importer.

Stored as ``uploads/.watcher_config.json``. The file holds the
operational parameters of ``ECGFolderWatcher`` (folder, auto-analyze
flag, delete-after flag, stability window) so that choices made via
the UI modal survive a server restart, even when the corresponding
environment variables are not set.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)

_CONFIG_FILENAME = '.watcher_config.json'


class WatcherConfig:
    """Thread-safe wrapper around the watcher config JSON file."""

    def __init__(self, upload_folder: str | os.PathLike):
        self._path = Path(upload_folder) / _CONFIG_FILENAME
        self._lock = Lock()
        self._data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        """Read the JSON file. Returns an empty dict if missing or invalid."""
        with self._lock:
            if not self._path.exists():
                self._data = {}
                return {}
            try:
                with open(self._path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    raw = {}
                self._data = raw
            except Exception as e:
                log.exception('Failed to load watcher config: %s', e)
                self._data = {}
            return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def save(self, config: dict[str, Any]) -> None:
        """Atomically replace the on-disk config with ``config``."""
        with self._lock:
            self._data = dict(config)
            self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix='.watcher_config.', suffix='.json',
                dir=str(self._path.parent),
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
            log.exception('Failed to persist watcher config: %s', e)
