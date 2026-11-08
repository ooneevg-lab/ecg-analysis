"""
Heart-rate-only rhythm classifier.

The rhythm pipeline deliberately does not use NeuroKit, HRV rules, or a separate
rhythm model. It estimates heart rate from R-peaks on the first 10000 samples of
the 500 Hz resampled signal and applies three rules:
- HR < 60: bradycardia
- 60 <= HR <= 100: normal
- HR > 100: tachycardia
"""
import sys
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from utils.signal_processing import CHANNEL_NAMES as LEAD_NAMES  # noqa: E402

FS = 500
RHYTHM_SAMPLES = 10000


class RhythmClassificationError(Exception):
    """Exception raised when rhythm classification fails."""


class RhythmClassifier:
    """Classify rhythm from heart rate only."""

    def __init__(self, config_path: str | None = None, window_samples: int = RHYTHM_SAMPLES):
        self.config_path = config_path
        self.thr_brad = 60.0
        self.thr_tach = 100.0
        self.window_samples = int(window_samples)

    def analyze(self, ecg_data: np.ndarray, fs: int = FS) -> dict:
        if ecg_data.shape[0] != 12 and ecg_data.shape[1] == 12:
            ecg_data = ecg_data.T

        fs = int(fs)
        window_samples = min(int(self.window_samples), int(ecg_data.shape[1]))
        if window_samples < int(fs * 3):
            raise RhythmClassificationError("Недостаточно данных для расчета ЧСС")

        window = ecg_data[:, :window_samples].astype(np.float64, copy=False)
        peaks, best_channel = self._find_best_peaks(window, fs)
        if len(peaks) < 2:
            raise RhythmClassificationError("Недостаточно R-пиков для расчета ЧСС")

        rr_sec = np.diff(peaks).astype(np.float64) / float(fs)
        rr_sec = rr_sec[(rr_sec >= 0.3) & (rr_sec <= 2.5)]
        if len(rr_sec) == 0:
            raise RhythmClassificationError("Не удалось получить корректные интервалы R-R")

        hr_bpm = float(60.0 / np.mean(rr_sec))
        classification = self._classify_hr(hr_bpm)
        duration_sec = float(window_samples) / float(fs)

        return {
            **classification,
            "hr_bpm": hr_bpm,
            "r_peaks": peaks.tolist(),
            "n_peaks": int(len(peaks)),
            "best_channel": best_channel,
            "rhythm_window_samples": int(window_samples),
            "rhythm_window_sec": round(duration_sec, 3),
            "sampling_rate": fs,
            "triggered_rules": classification["triggered_rules"],
        }

    def _classify_hr(self, hr_bpm: float) -> dict:
        if hr_bpm < self.thr_brad:
            return {
                "rhythm_class": "bradycardia",
                "rhythm_class_ru": "Брадикардия",
                "triggered_rules": [f"ЧСС {hr_bpm:.0f} < {self.thr_brad:.0f} уд/мин"],
            }
        if hr_bpm > self.thr_tach:
            return {
                "rhythm_class": "tachycardia",
                "rhythm_class_ru": "Тахикардия",
                "triggered_rules": [f"ЧСС {hr_bpm:.0f} > {self.thr_tach:.0f} уд/мин"],
            }
        return {
            "rhythm_class": "normal",
            "rhythm_class_ru": "Норма",
            "triggered_rules": [],
        }

    def _find_best_peaks(self, data: np.ndarray, fs: int) -> tuple[np.ndarray, str]:
        candidates: list[tuple[float, np.ndarray, int]] = []

        preferred_order = [1, 0, 5, 6, 7, 8, 9, 10, 11, 2, 4, 3]
        for idx in preferred_order:
            ch = data[idx]
            if np.allclose(ch, 0) or np.nanstd(ch) <= 1e-8:
                continue
            peaks = self._detect_peaks(ch, fs)
            if len(peaks) < 2:
                continue

            rr = np.diff(peaks).astype(np.float64) / float(fs)
            rr = rr[(rr >= 0.3) & (rr <= 2.5)]
            if len(rr) == 0:
                continue

            regularity = float(np.std(rr) / (np.mean(rr) + 1e-8))
            peak_rate = len(peaks) / (data.shape[1] / float(fs))
            plausible_rate = 0.5 <= peak_rate <= 3.0
            score = (1.0 / (regularity + 0.05)) + (2.0 if plausible_rate else 0.0) + min(len(peaks), 20) * 0.01
            candidates.append((score, peaks, idx))

        if not candidates:
            raise RhythmClassificationError("R-пики не найдены")

        candidates.sort(key=lambda item: item[0], reverse=True)
        _, peaks, idx = candidates[0]
        return peaks.astype(int), LEAD_NAMES[idx]

    def _detect_peaks(self, ch: np.ndarray, fs: int) -> np.ndarray:
        signal = np.nan_to_num(ch.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
        signal = signal - np.median(signal)

        # Use absolute value so inverted QRS complexes remain detectable.
        work = np.abs(signal)
        spread = np.percentile(work, 95) - np.percentile(work, 50)
        if spread <= 1e-8:
            return np.array([], dtype=int)

        height = np.percentile(work, 75) + 0.35 * spread
        prominence = max(0.15 * spread, 1e-8)
        distance = int(0.3 * fs)

        peaks, _ = find_peaks(work, height=height, prominence=prominence, distance=distance)
        return peaks.astype(int)
