"""
ECG preprocessing service.

Runtime pipeline:
1. Validate input signal.
2. Resample the whole recording to the target sampling rate.
3. Keep a fixed preview window for model inference and fast plotting.
4. Compute signal quality metrics on the preview window.
"""
import sys
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from utils.signal_processing import (
    CHANNEL_NAMES,
    FS as TARGET_FS,
    assess_morphology,
    compute_sqi,
    filter_ecg,
    validate_signal,
)


TARGET_SAMPLES = 5000
MIN_SAMPLES_AFTER_RESAMPLE = 5000


class PreprocessingError(Exception):
    """Exception raised when preprocessing fails."""


class ECGPreprocessor:
    """Service for runtime ECG preprocessing."""

    def __init__(self, target_fs: int = TARGET_FS, target_samples: int = TARGET_SAMPLES):
        self.target_fs = int(target_fs)
        self.target_samples = int(target_samples)

    def preprocess(
        self,
        leads: np.ndarray,
        source_fs: int,
        apply_filter: bool = False,
        apply_notch: bool = False,
        notch_freq: float = 50.0,
    ) -> dict:
        """
        Resample the whole ECG and prepare the model/display preview.

        Args:
            leads: ECG data [12, T] or [T, 12] in volts.
            source_fs: Source sampling rate in Hz.
            apply_filter: Apply SciPy bandpass filtering to the preview only.
            apply_notch: Apply SciPy notch filtering to the preview only.
            notch_freq: Notch frequency.

        Returns:
            Dictionary with full resampled signal, preview window and quality metrics.
        """
        if leads.shape[0] != 12 and leads.shape[1] == 12:
            leads = leads.T

        is_valid, issues = validate_signal(
            leads,
            fs=source_fs,
            min_duration_sec=1.0,
            max_zero_channels=2,
        )

        if int(source_fs) != self.target_fs:
            leads = self._resample(leads, int(source_fs), self.target_fs)
        else:
            leads = leads.astype(np.float32, copy=True)

        if leads.shape[1] < MIN_SAMPLES_AFTER_RESAMPLE:
            return {
                "leads": None,
                "leads_raw": None,
                "leads_full": None,
                "sampling_rate": self.target_fs,
                "sqi_per_lead": None,
                "overall_sqi": 0,
                "morphology_pass": False,
                "bad_channels": [],
                "morphology_details": {},
                "validation_issues": issues,
                "is_valid": False,
                "skipped": True,
                "skip_reason": (
                    f"Слишком короткая запись: {leads.shape[1]} отсчетов после "
                    f"ресемплинга, нужно минимум {MIN_SAMPLES_AFTER_RESAMPLE}"
                ),
            }

        leads_full = leads.astype(np.float32, copy=True)
        leads_raw = leads_full[:, :self.target_samples].copy()

        if apply_filter or apply_notch:
            leads_preview = filter_ecg(
                leads_raw.copy(),
                fs=self.target_fs,
                bandpass=apply_filter,
                notch=apply_notch,
                notch_freq=notch_freq,
            )
        else:
            leads_preview = leads_raw.copy()

        sqi_per_lead, overall_sqi = compute_sqi(leads_preview, fs=self.target_fs)
        morph_pass, bad_channels, morph_details = assess_morphology(leads_preview)

        return {
            "leads": leads_preview.astype(np.float32),
            "leads_raw": leads_raw.astype(np.float32),
            "leads_full": leads_full.astype(np.float32),
            "sampling_rate": self.target_fs,
            "sqi_per_lead": sqi_per_lead,
            "overall_sqi": overall_sqi,
            "morphology_pass": morph_pass,
            "bad_channels": bad_channels,
            "morphology_details": morph_details,
            "validation_issues": issues,
            "is_valid": is_valid,
            "skipped": False,
        }

    def _resample(self, leads: np.ndarray, fs_from: int, fs_to: int) -> np.ndarray:
        """Resample ECG data to a new sampling rate."""
        g = gcd(int(fs_to), int(fs_from))
        up = int(fs_to) // g
        down = int(fs_from) // g
        out_len = int(round(leads.shape[1] * fs_to / fs_from))
        out = np.zeros((12, out_len), dtype=np.float32)

        for i in range(12):
            resampled = resample_poly(leads[i].astype(np.float64), up, down)
            n = min(out_len, len(resampled))
            out[i, :n] = resampled[:n].astype(np.float32)

        return out

    def quick_validate(
        self,
        leads: np.ndarray,
        fs: int,
        min_duration_sec: float = 10.0,
    ) -> tuple[bool, dict]:
        """Quick validation without full preprocessing."""
        return validate_signal(leads, fs=fs, min_duration_sec=min_duration_sec, max_zero_channels=0)

    def get_channel_names(self) -> list[str]:
        """Return standard 12-lead channel names."""
        return CHANNEL_NAMES.copy()
