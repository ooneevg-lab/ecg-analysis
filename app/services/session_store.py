"""
Persistent session state for uploaded/imported ECG records.

The runtime still keeps records in memory for fast access. This store mirrors
that state to uploads/.session_state.json so files, parsed signals, analysis
results, archive flags, and the single UI threshold survive a server restart.

Full signals (ecg_data, ecg_data_full, r_peaks, saliency_map) are NOT persisted
to keep the JSON lightweight. They are reloaded on-demand from the source XML
file (if available via source_path) or require manual re-upload.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

log = logging.getLogger(__name__)

_SESSION_FILENAME = ".session_state.json"
# Keys to persist in full (not just summary) for analysis results.
# NOTE: Full signals (ecg_data, ecg_data_raw, ecg_data_full, r_peaks, saliency_map)
# are intentionally EXCLUDED to keep JSON lightweight. They are reloaded on-demand
# from the source XML file (if available via source_path) or require manual re-upload.
_FULL_ANALYSIS_KEYS = {
    "file_id",
    "filename",
    "class_names",
    "probabilities",
    "argmax_label",
    "argmax_class",
    "pred_label",
    "pred_class",
    "threshold_applied",
    "threshold_rule",
    "probability",
    "pathology_probability",
    "prediction",
    "verdict",
    "confidence",
    "class_probability",
    "threshold",
    "threshold_rules",
    "source_sampling_rate",
    "sampling_rate",
    "resampled_duration_sec",
    "rhythm_class",
    "rhythm_class_ru",
    "hr_bpm",
    "triggered_rules",
    "rhythm_window_samples",
    "rhythm_window_sec",
    "overall_sqi",
    "morphology_pass",
    "bad_channels",
    "norm_mode",
    "runtime_normalization",
    "model_input_sampling_rate",
    "model_input_samples",
    "model_input_units",
    "model_input_scale",
    "resampled_samples",
    # XAI: only top_leads and method (saliency_map excluded)
    "xai",
}


def _to_jsonable(value: Any) -> Any:
    try:
        import numpy as np
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    except Exception:
        pass
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _from_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _from_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_jsonable(v) for v in value]
    return value


def _analysis_summary(result: Any) -> dict | None:
    if not isinstance(result, dict):
        return None
    # Return full analysis result for persistence (all keys in _FULL_ANALYSIS_KEYS)
    summary = {k: result.get(k) for k in _FULL_ANALYSIS_KEYS if k in result}
    # Strip heavy fields from xai if present
    if "xai" in summary and isinstance(summary["xai"], dict):
        xai = summary["xai"]
        # Remove saliency_map to keep JSON lightweight
        xai.pop("saliency_map", None)
        # Keep only lightweight xai fields
        xai = {k: xai.get(k) for k in ("top_leads", "method", "error") if k in xai}
        summary["xai"] = xai if xai else None
    return summary if summary else None


def _compact_record(record: dict) -> dict:
    compact = {
        "filename": record.get("filename"),
        "temp_path": record.get("temp_path"),
        "is_valid": bool(record.get("is_valid", False)),
        "validation_errors": list(record.get("validation_errors") or []),
        "parse_error": record.get("parse_error"),
        "analyzed": bool(record.get("analyzed", False)),
        "viewed": bool(record.get("viewed", False)),
        "archived": bool(record.get("archived", False)),
        "created_at": record.get("created_at"),
        "source": record.get("source", "upload"),
        "source_path": record.get("source_path"),
        "auto_imported": bool(record.get("auto_imported", False)),
        "parsed": None,
        "analysis_result": None,
    }
    if record.get("analyzed") and record.get("analysis_result"):
        summary = _analysis_summary(record.get("analysis_result"))
        if summary:
            compact["analysis_result"] = summary
            compact["analyzed"] = True
    return compact


class SessionStore:
    """Thread-safe JSON store for session records and shared settings."""

    def __init__(self, upload_folder: str | os.PathLike):
        self._path = Path(upload_folder) / _SESSION_FILENAME
        self._lock = RLock()
        self._data: dict[str, Any] = {
            "records": {},
            "settings": {},
        }

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self._path.exists():
                self._data = {"records": {}, "settings": {}}
                return self.snapshot()
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    raw = {}
                self._data = {
                    "records": _from_jsonable(raw.get("records", {})) if isinstance(raw.get("records", {}), dict) else {},
                    "settings": raw.get("settings", {}) if isinstance(raw.get("settings", {}), dict) else {},
                }
            except Exception as e:
                log.warning("Failed to load session state: %s", e)
                self._data = {"records": {}, "settings": {}}
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "records": dict(self._data.get("records", {})),
                "settings": dict(self._data.get("settings", {})),
            }

    def records(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._data.get("records", {}))

    def get_full_records(self) -> dict[str, dict]:
        """Get records with full analysis results restored from persistence."""
        with self._lock:
            return dict(self._data.get("records", {}))

    def get_analysis_result(self, file_id: str) -> dict | None:
        """Get the full analysis result for a specific file."""
        with self._lock:
            record = self._data.get("records", {}).get(file_id)
            if record and record.get("analyzed") and record.get("analysis_result"):
                return dict(record["analysis_result"])
            return None

    def save_record(self, file_id: str, record: dict) -> None:
        with self._lock:
            self._data.setdefault("records", {})[file_id] = _compact_record(record)
            self._flush_unlocked()

    def save_records(self, records: dict[str, dict]) -> None:
        with self._lock:
            self._data["records"] = {
                file_id: _compact_record(record)
                for file_id, record in records.items()
            }
            self._flush_unlocked()

    def delete_record(self, file_id: str) -> None:
        with self._lock:
            self._data.setdefault("records", {}).pop(file_id, None)
            self._flush_unlocked()

    def clear_records(self) -> None:
        with self._lock:
            self._data["records"] = {}
            self._flush_unlocked()

    def set_record_flag(self, file_id: str, **flags: Any) -> bool:
        with self._lock:
            record = self._data.setdefault("records", {}).get(file_id)
            if record is None:
                return False
            record.update(flags)
            self._flush_unlocked()
            return True

    def get_threshold(self, default: float = 0.35) -> float:
        with self._lock:
            value = self._data.setdefault("settings", {}).get("classification_threshold", default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def set_threshold(self, threshold: float) -> float:
        value = max(0.0, min(1.0, float(threshold)))
        with self._lock:
            self._data.setdefault("settings", {})["classification_threshold"] = value
            self._flush_unlocked()
        return value

    def get_upload_folder_override(self) -> str | None:
        with self._lock:
            return self._data.get("settings", {}).get("upload_folder_override")

    def set_upload_folder_override(self, folder: str | None) -> str | None:
        with self._lock:
            settings = self._data.setdefault("settings", {})
            if folder:
                settings["upload_folder_override"] = folder
            else:
                settings.pop("upload_folder_override", None)
            self._flush_unlocked()
        return folder

    def clear_all(self) -> None:
        with self._lock:
            self._data = {"records": {}, "settings": self._data.get("settings", {})}
            self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = _to_jsonable(self._data)
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=".session_state.", suffix=".json", dir=str(self._path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            log.warning("Failed to persist session state: %s", e)
