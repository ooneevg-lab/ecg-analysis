"""Signal cache - disabled (no-op stubs to avoid import errors).

Full signals are now always computed on-demand from source XML.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def cache_dir(upload_folder: str | os.PathLike) -> Path:
    return Path(upload_folder) / "signal_cache"


def cache_path(upload_folder: str | os.PathLike, file_id: str) -> Path:
    return cache_dir(upload_folder) / f"{file_id}.json"


def save_signal_cache(upload_folder: str | os.PathLike, file_id: str, signal: dict) -> None:
    """No-op: signal caching disabled. Signals computed on-demand from XML."""
    pass


def load_signal_cache(upload_folder: str | os.PathLike, file_id: str) -> dict | None:
    """No-op: signal caching disabled. Always returns None to force recomputation."""
    return None


def delete_signal_cache(upload_folder: str | os.PathLike, file_id: str) -> None:
    """No-op: signal caching disabled."""
    pass
