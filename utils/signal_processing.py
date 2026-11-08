"""
Signal processing utilities for ECG data.

Includes:
  - Signal validation (NaN, Inf, all-zero, min length)
  - Filtering (bandpass, notch, baseline wander removal)
  - Signal Quality Index (SQI) computation
  - Artifact detection and masking
  - Resampling
  - Morphology-based TC (Template Correlation) assessment
  - Channel zeroing for bad leads

Consolidated from:
  - ecg_morphology_filter_v33.py  → TC metrics, morphology assessment
  - train_simple_cnn.py           → R-peak detection helpers
  - New implementations            → filtering, SQI, resampling
"""

from pathlib import Path

import numpy as np
from scipy.signal import (
    butter, filtfilt, find_peaks, iirnotch, resample_poly, correlate,
)

# ECG constants
FS          = 500
N_SAMPLES   = 5000
N_CHANNELS  = 12
CHANNEL_NAMES = ["I", "II", "III", "aVR", "aVL", "aVF",
                 "V1", "V2", "V3", "V4", "V5", "V6"]

# TC (Template Correlation) parameters
TC_BEAT_LEFT_SEC  = 0.20
TC_BEAT_RIGHT_SEC = 0.36
TC_MAX_LAG_SEC    = 0.030
TC_THRESH        = 0.70
TC_THRESH_WEAK   = 0.55
TC_WEAK_CHANNELS = {"aVR", "III", "V1"}
TC_MIN_BEATS     = 4
BAD_CH_THRESH_TC = 6


# ---------------------------------------------------------------------------
# 1. Signal validation
# ---------------------------------------------------------------------------

def validate_signal(data: np.ndarray, fs: float = FS,
                    min_duration_sec: float = 5.0,
                    max_zero_channels: int = 0) -> tuple[bool, dict]:
    """Validate a 12-lead ECG recording for common issues.

    Args:
        data: shape [12, T] or [T, 12]
        fs: sampling rate in Hz
        min_duration_sec: minimum recording length
        max_zero_channels: max allowed all-zero channels (0 = strict)

    Returns:
        (is_valid, issues_dict)
        issues keys: "all_zero_ch", "nan_ch", "inf_ch", "short", "flat_ch"
    """
    issues = {
        "all_zero_ch": [],
        "nan_ch": [],
        "inf_ch": [],
        "short": False,
        "flat_ch": [],
    }

    # Ensure [12, T]
    if data.ndim != 2:
        return False, {"error": "Expected 2D array [12, T] or [T, 12]"}

    if data.shape[0] != 12 and data.shape[1] == 12:
        data = data.T

    n_leads = data.shape[0]
    min_samples = int(fs * min_duration_sec)

    # Check length
    if data.shape[1] < min_samples:
        issues["short"] = True

    for i in range(n_leads):
        ch = data[i]
        if np.all(ch == 0):
            issues["all_zero_ch"].append(i)
        if np.any(np.isnan(ch)):
            issues["nan_ch"].append(i)
        if np.any(np.isinf(ch)):
            issues["inf_ch"].append(i)
        # Flat line check (std ≈ 0 but not all zeros)
        if not np.all(ch == 0) and np.std(ch) < 1e-6:
            issues["flat_ch"].append(i)

    is_valid = (
        len(issues["all_zero_ch"]) <= max_zero_channels
        and not issues["nan_ch"]
        and not issues["inf_ch"]
        and not issues["short"]
    )
    return is_valid, issues


# ---------------------------------------------------------------------------
# 2. Filtering
# ---------------------------------------------------------------------------

def bandpass_filter(ch: np.ndarray, fs: float = FS,
                    low: float = 0.5, high: float = 40.0,
                    order: int = 4) -> np.ndarray:
    """Butterworth bandpass filter (zero-phase via filtfilt).

    Args:
        ch: 1D signal
        fs: sampling rate (Hz)
        low: low cutoff (Hz) — removes baseline wander
        high: high cutoff (Hz) — removes high-frequency noise
        order: filter order

    Returns:
        Filtered signal, same shape as input
    """
    nyq = fs / 2.0
    low_norm  = max(low / nyq, 0.001)
    high_norm = min(high / nyq, 0.999)
    b, a = butter(order, [low_norm, high_norm], btype="band")
    return filtfilt(b, a, ch).astype(ch.dtype)


def notch_filter(ch: np.ndarray, fs: float = FS,
                 freq: float = 50.0, quality: float = 30.0) -> np.ndarray:
    """IIR notch filter to remove powerline interference.

    Args:
        ch: 1D signal
        fs: sampling rate (Hz)
        freq: notch frequency (50 Hz Europe, 60 Hz US)
        quality: Q factor — higher = narrower notch

    Returns:
        Filtered signal
    """
    nyq = fs / 2.0
    b, a = iirnotch(freq / nyq, quality)
    return filtfilt(b, a, ch).astype(ch.dtype)


def remove_baseline_wander(ch: np.ndarray, fs: float = FS,
                           cutoff: float = 0.5) -> np.ndarray:
    """Remove baseline wander via high-pass filter.

    Simpler alternative to bandpass — only removes low-frequency drift.
    """
    nyq = fs / 2.0
    b, a = butter(4, max(cutoff / nyq, 0.001), btype="high")
    return filtfilt(b, a, ch).astype(ch.dtype)


def filter_ecg(data: np.ndarray, fs: float = FS,
               bandpass: bool = True, notch: bool = True,
               notch_freq: float = 50.0) -> np.ndarray:
    """Apply standard ECG preprocessing pipeline to all 12 leads.

    Pipeline:
        1. Bandpass 0.5–40 Hz (removes baseline wander + HF noise)
        2. Notch 50 Hz (removes powerline interference)

    Args:
        data: [12, T] or [T, 12]
        fs: sampling rate
        bandpass: apply bandpass filter
        notch: apply notch filter
        notch_freq: notch frequency (50 or 60 Hz)

    Returns:
        Filtered data, same shape
    """
    if data.ndim != 2:
        raise ValueError("Expected 2D array")

    if data.shape[0] != 12 and data.shape[1] == 12:
        data = data.T

    out = data.copy().astype(np.float64)
    for i in range(data.shape[0]):
        ch = data[i]
        if np.all(ch == 0):
            continue  # skip dead channels
        if bandpass:
            ch = bandpass_filter(ch, fs)
        if notch:
            ch = notch_filter(ch, fs, freq=notch_freq)
        out[i] = ch
    return out.astype(data.dtype)


# ---------------------------------------------------------------------------
# 3. Signal Quality Index (SQI)
# ---------------------------------------------------------------------------

def _kurtosis_sqi(ch: np.ndarray, window_sec: float = 2.0,
                  fs: float = FS) -> float:
    """SQI based on kurtosis of sliding windows.

    ECG with clean QRS complexes has high kurtosis (spiky signal).
    Noisy signals have kurtosis closer to 0 (Gaussian).

    Returns:
        SQI in [0, 1] — higher = cleaner
    """
    win = int(fs * window_sec)
    if len(ch) < win:
        return 0.0

    from scipy.stats import kurtosis

    n_windows = len(ch) // win
    if n_windows == 0:
        return 0.0

    kurt_vals = []
    for i in range(n_windows):
        seg = ch[i * win: (i + 1) * win]
        if np.std(seg) > 1e-8:
            kurt_vals.append(float(kurtosis(seg)))

    if not kurt_vals:
        return 0.0

    # Map kurtosis [−2, +∞) → [0, 1]; typical ECG kurtosis ≈ 3–15
    median_k = np.median(kurt_vals)
    return float(np.clip(median_k / 10.0, 0.0, 1.0))


def _spectral_sqi(ch: np.ndarray, fs: float = FS,
                  qrs_band: tuple = (5.0, 25.0)) -> float:
    """SQI based on spectral energy ratio in QRS band vs total.

    Clean ECG concentrates energy in 5–25 Hz (QRS complex).

    Returns:
        SQI in [0, 1] — higher = more energy in QRS band
    """
    from scipy.fft import rfft, rfftfreq

    fft_vals = np.abs(rfft(ch))
    freqs = rfftfreq(len(ch), d=1.0 / fs)

    qrs_mask = (freqs >= qrs_band[0]) & (freqs <= qrs_band[1])
    total_energy = np.sum(fft_vals ** 2)
    qrs_energy = np.sum(fft_vals[qrs_mask] ** 2)

    return float(qrs_energy / (total_energy + 1e-10))


def compute_sqi(data: np.ndarray, fs: float = FS) -> tuple[np.ndarray, np.ndarray]:
    """Compute Signal Quality Index for each lead.

    Combines kurtosis-based SQI and spectral SQI into a composite score.

    Args:
        data: [12, T] or [T, 12]

    Returns:
        (sqi_per_lead [12], overall_sqi [scalar])
        SQI values in [0, 1] — higher = better quality
    """
    if data.shape[0] != 12 and data.shape[1] == 12:
        data = data.T

    sqi_scores = []
    for i in range(12):
        ch = data[i].astype(np.float64)
        if np.all(ch == 0):
            sqi_scores.append(0.0)
            continue
        ch_norm = (ch - np.mean(ch)) / (np.std(ch) + 1e-8)
        k_sqi = _kurtosis_sqi(ch_norm, fs=fs)
        s_sqi = _spectral_sqi(ch_norm, fs=fs)
        sqi_scores.append(float(0.5 * k_sqi + 0.5 * s_sqi))

    sqi_arr = np.array(sqi_scores, dtype=np.float32)
    return sqi_arr, float(np.mean(sqi_arr))


# ---------------------------------------------------------------------------
# 4. Resampling
# ---------------------------------------------------------------------------

def resample_ecg(data: np.ndarray, fs_from: float, fs_to: float = FS,
                 target_length: int = N_SAMPLES) -> np.ndarray:
    """Resample ECG to a target sampling rate and/or length.

    Args:
        data: [12, T] or [T, 12]
        fs_from: original sampling rate
        fs_to: target sampling rate
        target_length: if specified, truncate/pad to this length after resampling

    Returns:
        Resampled data [12, target_length] (or natural length if not specified)
    """
    if data.shape[0] != 12 and data.shape[1] == 12:
        data = data.T

    # --- Старый код (удалён, т.к. out инициализировался с нулевой длиной) ---
    # ratio = fs_to / fs_from
    # out = np.empty((12, 0), dtype=np.float32)  # ← ОШИБКА: shape (12, 0) не позволяет записать данные
    #
    # for i in range(12):
    #     ch_resampled = resample_poly(data[i].astype(np.float64), fs_to, fs_from)
    #     out[i] = ch_resampled.astype(np.float32)  # ValueError: could not broadcast

    # --- Исправлено: собираем каналы в список, затем stack ---
    channels = []
    for i in range(12):
        ch_resampled = resample_poly(data[i].astype(np.float64), fs_to, fs_from)
        channels.append(ch_resampled.astype(np.float32))

    # Stack into [12, T] array
    out = np.stack(channels, axis=0)

    if target_length > 0:
        if out.shape[1] > target_length:
            out = out[:, :target_length]
        elif out.shape[1] < target_length:
            # Pad with zeros
            pad = np.zeros((12, target_length - out.shape[1]), dtype=np.float32)
            out = np.concatenate([out, pad], axis=1)

    return out


# ---------------------------------------------------------------------------
# 5. Artifact detection
# ---------------------------------------------------------------------------

def detect_artifacts(data: np.ndarray, fs: float = FS,
                     amplitude_threshold_std: float = 6.0,
                     window_sec: float = 1.0) -> np.ndarray:
    """Detect segments with abnormally high amplitude (motion artifacts, electrode pop).

    Args:
        data: [12, T] or [T, 12]
        fs: sampling rate
        amplitude_threshold_std: segments with amplitude > this × std are artifacts
        window_sec: analysis window size

    Returns:
        artifact_mask: bool array [12, T] — True where artifact detected
    """
    if data.shape[0] != 12 and data.shape[1] == 12:
        data = data.T

    win = int(fs * window_sec)
    n_leads, n_samples = data.shape
    mask = np.zeros_like(data, dtype=bool)

    for i in range(n_leads):
        ch = data[i]
        if np.all(ch == 0):
            continue
        ch_std = np.std(ch)
        if ch_std < 1e-8:
            continue
        threshold = ch_std * amplitude_threshold_std

        for start in range(0, n_samples, win):
            seg = ch[start: start + win]
            if np.max(np.abs(seg)) > threshold:
                mask[i, start: start + win] = True

    return mask


# ---------------------------------------------------------------------------
# 6. Template Correlation (TC) — morphology assessment
# ---------------------------------------------------------------------------

def normalize_channel(ch: np.ndarray) -> np.ndarray:
    """Z-score normalization of one ECG lead."""
    mu = float(np.mean(ch))
    sig = float(np.std(ch))
    if sig < 1e-6:
        return np.zeros_like(ch, dtype=np.float32)
    return ((ch.astype(np.float64) - mu) / sig).astype(np.float32)


def _orient_signal(ch: np.ndarray) -> np.ndarray:
    """Flip if the negative lobe is larger than the positive lobe."""
    if float(np.max(ch)) < abs(float(np.min(ch))):
        return -ch
    return ch


def _rr_quality(peaks: np.ndarray) -> float:
    """CV of RR intervals. Lower = more regular rhythm."""
    if len(peaks) < 3:
        return float("inf")
    rr = np.diff(peaks).astype(float)
    return float(rr.std() / rr.mean()) if rr.mean() > 0 else float("inf")


def _peak_snr(ch: np.ndarray, peaks: np.ndarray) -> float:
    """Inverse SNR: mean peak amplitude / RMS. Lower = better."""
    if len(peaks) == 0:
        return float("inf")
    rms = float(np.sqrt(np.mean(ch.astype(np.float64) ** 2)))
    if rms < 1e-10:
        return float("inf")
    peak_amps = np.abs(ch[peaks].astype(np.float64))
    snr = float(np.mean(peak_amps)) / rms
    return 1.0 / snr if snr > 1e-10 else float("inf")


def _peak_sharpness(ch: np.ndarray, peaks: np.ndarray) -> float:
    """Average peak width at half-max, normalised. Lower = sharper."""
    if len(peaks) == 0:
        return float("inf")
    half_window = int(FS * 0.15)
    widths = []
    for p in peaks:
        lo = max(0, p - half_window)
        hi = min(len(ch), p + half_window)
        segment = np.abs(ch[lo:hi].astype(np.float64))
        if len(segment) == 0:
            continue
        peak_val = float(np.max(segment))
        if peak_val < 1e-10:
            continue
        widths.append(int(np.sum(segment >= peak_val * 0.5)))
    if not widths:
        return float("inf")
    return float(np.mean(widths)) / 100.0


def _channel_score(ch: np.ndarray, peaks: np.ndarray) -> float:
    """Composite channel quality. Lower = better.

    score = CV_RR × 2.0 + (1/SNR) × 1.0 + sharpness × 1.0
    """
    cv = _rr_quality(peaks)
    inv_snr = _peak_snr(ch, peaks)
    sharpness = _peak_sharpness(ch, peaks)
    if cv == float("inf"):
        return float("inf")
    return cv * 2.0 + inv_snr * 1.0 + sharpness * 1.0


def _try_detect(ch_norm: np.ndarray) -> tuple[np.ndarray, float]:
    """Detect R-peaks using SciPy only."""
    ch_oriented = _orient_signal(ch_norm)
    work = np.abs(ch_oriented - np.median(ch_oriented))
    spread = np.percentile(work, 95) - np.percentile(work, 50)
    if spread <= 1e-8:
        return np.array([], dtype=int), float("inf")

    peaks, _ = find_peaks(
        work,
        height=np.percentile(work, 75) + 0.35 * spread,
        prominence=max(0.15 * spread, 1e-8),
        distance=int(0.3 * FS),
    )
    peaks = np.asarray(peaks, dtype=int)
    if len(peaks) < TC_MIN_BEATS:
        return np.array([], dtype=int), float("inf")
    return peaks, _channel_score(ch_oriented, peaks)


def find_best_peaks(data: np.ndarray) -> tuple[np.ndarray, str, float]:
    """Select the best lead for R-peak detection.

    Args:
        data: shape [T, 12] — time-first ECG array.

    Returns:
        (peaks, channel_name, rr_cv)
    """
    # Handle [12, T] → [T, 12]
    if data.shape[0] == N_CHANNELS:
        data = data.T

    best_peaks = np.array([], dtype=int)
    best_score = float("inf")
    best_cv = float("inf")
    best_ch = ""

    for i in range(N_CHANNELS):
        ch_norm = normalize_channel(data[:, i])
        peaks, score = _try_detect(ch_norm)
        if len(peaks) >= TC_MIN_BEATS and score < best_score:
            best_peaks = peaks
            best_score = score
            best_ch = CHANNEL_NAMES[i]
            best_cv = _rr_quality(peaks)

    return best_peaks, best_ch, best_cv


def _xcorr_similarity(b1: np.ndarray, b2: np.ndarray,
                      max_lag: int) -> float:
    """Normalised cross-correlation similarity in a lag window.

    Returns value in [-1, 1], higher = more similar.
    """
    corr = correlate(b1, b2, mode="full")
    mid = len(corr) // 2
    window = corr[mid - max_lag: mid + max_lag + 1]
    norm = float(np.sqrt(np.sum(b1 ** 2) * np.sum(b2 ** 2)))
    return float(window.max() / norm) if norm > 1e-10 else 0.0


def compute_tc_for_channel(ch_norm: np.ndarray, peaks: np.ndarray) -> float:
    """Template Correlation for one channel.

    Computes the average cross-correlation between consecutive
    QRS complexes (z-scored beats), allowing small time shifts.

    Args:
        ch_norm: normalised channel signal
        peaks: R-peak indices (shared across all channels)

    Returns:
        TC value in [0, 1] — higher = more consistent morphology
    """
    left = int(FS * TC_BEAT_LEFT_SEC)
    right = int(FS * TC_BEAT_RIGHT_SEC)
    max_lag = int(FS * TC_MAX_LAG_SEC)
    n = len(ch_norm)

    valid = [p for p in peaks if p - left >= 0 and p + right < n]
    if len(valid) < TC_MIN_BEATS:
        return 0.0
    inner = valid[1:-1]
    if len(inner) < 2:
        return 0.0

    def zscore(x: np.ndarray) -> np.ndarray:
        s = np.std(x)
        return (x - np.mean(x)) / s if s > 1e-6 else x

    beats_z = [zscore(ch_norm[p - left: p + right].astype(np.float64))
               for p in inner]
    corrs = []
    for i in range(len(beats_z) - 1):
        b1, b2 = beats_z[i], beats_z[i + 1]
        if np.std(b1) < 1e-6 or np.std(b2) < 1e-6:
            continue
        r = _xcorr_similarity(b1, b2, max_lag)
        if np.isfinite(r):
            corrs.append(r)
    return round(float(np.mean(corrs)), 4) if corrs else 0.0


def assess_morphology(data: np.ndarray) -> tuple[bool, list[str], dict]:
    """Assess ECG morphology quality using Template Correlation.

    Args:
        data: [12, T] or [T, 12]

    Returns:
        (is_pass, bad_channel_names, details_dict)
        - is_pass: True if < BAD_CH_THRESH_TC channels are bad
        - bad_channel_names: list of channels with TC below threshold
        - details_dict: {"peak_lead": str, "peak_cv": float,
                         "tc_per_channel": {name: tc_value}}
    """
    if data.shape[0] == N_CHANNELS and data.shape[1] != N_CHANNELS:
        data_t = data.T
    else:
        data_t = data

    peaks, best_ch, best_cv = find_best_peaks(data_t)
    if len(peaks) < TC_MIN_BEATS:
        return False, CHANNEL_NAMES[:], {
            "peak_lead": "", "peak_cv": None, "tc_per_channel": {},
            "error": "no_peaks",
        }

    bad_channels = []
    tc_per_ch = {}

    for i in range(N_CHANNELS):
        name = CHANNEL_NAMES[i]
        ch_norm = normalize_channel(data[i] if data.shape[0] == N_CHANNELS
                                    else data_t[:, i])
        tc = compute_tc_for_channel(ch_norm, peaks)
        tc_per_ch[name] = tc
        thresh = TC_THRESH_WEAK if name in TC_WEAK_CHANNELS else TC_THRESH
        if tc < thresh:
            bad_channels.append(name)

    is_pass = len(bad_channels) < BAD_CH_THRESH_TC
    details = {
        "peak_lead": best_ch,
        "peak_cv": round(best_cv, 4) if best_cv != float("inf") else None,
        "tc_per_channel": tc_per_ch,
    }
    return is_pass, bad_channels, details


def zero_bad_channels(data: np.ndarray, bad_channels: list[str]) -> np.ndarray:
    """Zero out specified channels in the data.

    Args:
        data: [12, T]
        bad_channels: list of channel names from CHANNEL_NAMES

    Returns:
        Data with bad channels set to zero
    """
    if data.shape[0] != 12:
        data = data.T
    out = data.copy()
    for name in bad_channels:
        if name in CHANNEL_NAMES:
            out[CHANNEL_NAMES.index(name)] = 0.0
    return out


# ---------------------------------------------------------------------------
# 7. File loading utilities
# ---------------------------------------------------------------------------

def load_ecg(filepath: str) -> np.ndarray | None:
    """Load ECG from .npy or .mat file. Returns [T, 12] or None."""
    try:
        p = Path(filepath)
        if p.suffix == ".npy":
            data = np.load(p)
        elif p.suffix == ".mat":
            from scipy.io import loadmat
            mat = loadmat(str(p))
            data = next(
                v for k, v in mat.items()
                if not k.startswith("_") and isinstance(v, np.ndarray) and v.ndim == 2
            )
        else:
            return None
        if data.shape == (N_SAMPLES, N_CHANNELS):
            return data.astype(np.float32)
        if data.shape == (N_CHANNELS, N_SAMPLES):
            return data.T.astype(np.float32)
    except Exception:
        pass
    return None
