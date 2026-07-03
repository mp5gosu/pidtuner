"""Gyro noise spectra via Welch's method (filtered vs unfiltered, per axis).

The purpose is noise-source hunting and Betaflight filter tuning: an averaged
amplitude spectrum reveals where (in Hz) the gyro energy sits, so the frontend
marker can read off motor/frame frequencies and set the gyro lowpass / dynamic
notch / RPM filter accordingly.

Amplitude is the RMS amplitude per frequency bin in deg/s (sqrt of Welch's
power spectrum with per-segment DC removal), so heights are physically
meaningful and directly comparable between filtered and unfiltered.
"""

import numpy as np
import pandas as pd
from scipy.signal import welch

from . import csv_parser

AXES = ("roll", "pitch", "yaw")


class SpectrumError(Exception):
    pass


def _resample_uniform(time_s: np.ndarray, sig: np.ndarray) -> np.ndarray:
    """Blackbox timestamps jitter slightly; Welch assumes uniform sampling, so
    resample onto an even grid spanning the same interval."""
    grid = np.linspace(time_s[0], time_s[-1], len(time_s))
    return np.interp(grid, time_s, sig)


def _welch_amp(sig: np.ndarray, fs: float, nperseg: int) -> tuple[np.ndarray, np.ndarray]:
    f, pxx = welch(sig, fs=fs, window="hann", nperseg=nperseg,
                   noverlap=nperseg // 2, scaling="spectrum")
    return f, np.sqrt(np.maximum(pxx, 0.0))


def compute(df: pd.DataFrame, session: dict) -> dict:
    """Per-axis amplitude spectra of the filtered and (if present) unfiltered
    gyro. Returns a single shared frequency axis plus filtered/unfiltered
    amplitude arrays per axis (absent signals -> None).
    """
    if "time" not in df.columns or len(df) < 256:
        raise SpectrumError("Log too short for spectral analysis (need > ~0.5 s).")

    time_us = df["time"].to_numpy()
    time_s = (time_us - time_us[0]) * 1e-6
    span = float(time_s[-1] - time_s[0])
    if span <= 0:
        raise SpectrumError("Log has no usable time base.")
    n = len(time_s)
    fs = (n - 1) / span

    # ~1 s window -> ~1 Hz resolution, averaged over every window in the flight.
    nperseg = int(min(n, max(256, round(fs))))

    scale = csv_parser.gyro_scale_factor(session.get("headers", {}))
    unfilt_source = session.get("gyro_unfilt_source", "")

    freq = None
    axes: dict[str, dict] = {}
    for i, axis in enumerate(AXES):
        entry = {"filtered": None, "unfiltered": None}

        filt_col = f"gyroADC[{i}]" if f"gyroADC[{i}]" in df.columns else f"gyroData[{i}]"
        if filt_col in df.columns:
            sig = _resample_uniform(time_s, df[filt_col].to_numpy() * scale)
            freq, entry["filtered"] = _welch_amp(sig, fs, nperseg)

        unfilt = None
        if unfilt_source == "gyroUnfilt" and f"gyroUnfilt[{i}]" in df.columns:
            unfilt = df[f"gyroUnfilt[{i}]"].to_numpy() * scale
        elif unfilt_source == "debug_gyro_scaled" and f"debug[{i}]" in df.columns:
            # GYRO_SCALED debug values are already deg/s
            unfilt = df[f"debug[{i}]"].to_numpy()
        if unfilt is not None:
            sig = _resample_uniform(time_s, unfilt)
            freq, entry["unfiltered"] = _welch_amp(sig, fs, nperseg)

        axes[axis] = entry

    if freq is None:
        raise SpectrumError("No gyro traces found in this log.")

    return {
        "fs": fs,
        "nyquist": fs / 2.0,
        "freq": freq,
        "unfilt_source": unfilt_source,
        "axes": axes,
    }
