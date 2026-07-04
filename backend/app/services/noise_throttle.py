"""Noise-vs-throttle heatmaps: gyro amplitude as a function of frequency and
throttle, per axis (the "throttle x frequency spectrogram" from PIDtoolbox).

Where the Noise Spectrum tab averages over the whole flight, this bins the
short-time spectra by the throttle at which they occurred. The result reveals
throttle-dependent noise - motor/frame resonances that ramp with RPM show up as
diagonal ridges, so you can see which throttle band a peak lives in and whether
the gyro lowpass / dynamic notch / RPM filter tames it there.

Method: a short-time FFT (scipy.signal.spectrogram) gives an amplitude column
per time segment; each segment is assigned the mean throttle over its span and
dropped into a throttle bin; columns in a bin are averaged. Empty bins are
filled from their neighbours and the map is lightly smoothed so it reads like
the continuous PIDtoolbox plot.

Amplitude is RMS per frequency bin in deg/s (sqrt of the 'spectrum'-scaled
power), matching services/spectrum.py so heights are physically meaningful and
filtered vs unfiltered are directly comparable.
"""

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter
from scipy.signal import spectrogram

from . import csv_parser

AXES = ("roll", "pitch", "yaw")

THROTTLE_BINS = 100      # 1 %-wide columns spanning 0..100 % throttle
TARGET_FREQ_RES_HZ = 4.0  # STFT window sized for ~this frequency resolution
MIN_SEGMENTS = 40        # need at least this many STFT windows to bin usefully
MAX_FREQ_ROWS = 512      # cap rows sent to the browser; decimate above this
NOISE_FLOOR_HZ = 30.0    # below this is craft motion, excluded from scale stats


class NoiseThrottleError(Exception):
    pass


def _resample_uniform(time_s: np.ndarray, sig: np.ndarray) -> np.ndarray:
    """Blackbox timestamps jitter; the STFT assumes uniform sampling."""
    grid = np.linspace(time_s[0], time_s[-1], len(time_s))
    return np.interp(grid, time_s, sig)


def _throttle_percent(df: pd.DataFrame) -> np.ndarray | None:
    """Throttle 0..100 %. rcCommand[3] is the 1000..2000 stick command;
    setpoint[3] (0..1000) is the fallback when it is absent."""
    if "rcCommand[3]" in df.columns:
        thr = (df["rcCommand[3]"].to_numpy() - 1000.0) / 10.0
    elif "setpoint[3]" in df.columns:
        thr = df["setpoint[3]"].to_numpy() / 10.0
    else:
        return None
    return np.clip(thr, 0.0, 100.0)


def _spectrogram_by_throttle(
    sig: np.ndarray, thr_grid: np.ndarray, fs: float, nperseg: int
) -> np.ndarray:
    """Amplitude spectrogram of `sig`, its columns averaged into throttle bins.

    Returns a [n_freq x THROTTLE_BINS] matrix. `thr_grid` is the throttle series
    on the same uniform grid as `sig`; each STFT segment takes the mean throttle
    over its span. Empty bins are left as NaN for the caller to fill.
    """
    freq, seg_t, sxx = spectrogram(
        sig, fs=fs, window="hann", nperseg=nperseg,
        noverlap=nperseg // 2, scaling="spectrum", mode="psd",
    )
    amp = np.sqrt(np.maximum(sxx, 0.0))  # [n_freq x n_seg]

    # Mean throttle over each segment's window (seg_t is the segment centre in s).
    grid_t = np.linspace(0.0, (len(sig) - 1) / fs, len(sig))
    seg_thr = np.interp(seg_t, grid_t, thr_grid)

    edges = np.linspace(0.0, 100.0, THROTTLE_BINS + 1)
    idx = np.clip(np.digitize(seg_thr, edges) - 1, 0, THROTTLE_BINS - 1)

    out = np.full((amp.shape[0], THROTTLE_BINS), np.nan, dtype=np.float64)
    for b in range(THROTTLE_BINS):
        cols = amp[:, idx == b]
        if cols.shape[1]:
            out[:, b] = cols.mean(axis=1)
    return freq, out


def _fill_and_smooth(grid: np.ndarray) -> np.ndarray:
    """Fill empty throttle bins from their nearest populated neighbour, then
    lightly smooth in both directions for a continuous PIDtoolbox-style image."""
    n_freq, n_thr = grid.shape
    filled = grid.copy()
    empty = np.all(np.isnan(filled), axis=0)
    if empty.all():
        return np.zeros_like(filled)
    if empty.any():
        good = np.flatnonzero(~empty)
        for b in np.flatnonzero(empty):
            src = good[np.argmin(np.abs(good - b))]
            filled[:, b] = filled[:, src]
    filled = np.nan_to_num(filled, nan=0.0)
    return gaussian_filter(filled, sigma=(1.0, 1.5), mode="nearest")


def _decimate_freq(freq: np.ndarray, grids: dict) -> tuple[np.ndarray, dict]:
    """Cap frequency rows to MAX_FREQ_ROWS by block-averaging (keeps payload
    bounded for high-rate logs without dropping peaks)."""
    n = len(freq)
    if n <= MAX_FREQ_ROWS:
        return freq, grids
    factor = int(np.ceil(n / MAX_FREQ_ROWS))
    usable = (n // factor) * factor
    fr = freq[:usable].reshape(-1, factor).mean(axis=1)
    out = {}
    for key, g in grids.items():
        if g is None:
            out[key] = None
            continue
        out[key] = {axis: m[:usable].reshape(-1, factor, m.shape[1]).mean(axis=1)
                    for axis, m in g.items()}
    return fr, out


def compute(df: pd.DataFrame, session: dict) -> dict:
    """Per-axis throttle x frequency amplitude maps for filtered and (if present)
    unfiltered gyro. Returns a shared freq axis + throttle axis and, per axis,
    a [freq x throttle] matrix for each signal (absent signals -> None).

    A single `vmax` (shared across every map) is returned for the colour scale,
    so filtered reads as visibly quieter than raw - the whole point of the view.
    """
    if "time" not in df.columns or len(df) < 512:
        raise NoiseThrottleError("Log too short for a throttle spectrogram (need > ~1 s).")

    thr = _throttle_percent(df)
    if thr is None:
        raise NoiseThrottleError("Log has no throttle channel (rcCommand[3]/setpoint[3]).")

    time_us = df["time"].to_numpy()
    time_s = (time_us - time_us[0]) * 1e-6
    span = float(time_s[-1] - time_s[0])
    if span <= 0:
        raise NoiseThrottleError("Log has no usable time base.")
    n = len(time_s)
    fs = (n - 1) / span

    # Window sized for ~TARGET_FREQ_RES_HZ resolution, but small enough to yield
    # at least MIN_SEGMENTS windows to bin by throttle.
    nperseg = int(fs / TARGET_FREQ_RES_HZ)
    nperseg = min(nperseg, n // (MIN_SEGMENTS // 2))
    nperseg = int(np.clip(nperseg, 64, n))
    if n // (nperseg // 2) < MIN_SEGMENTS:
        raise NoiseThrottleError("Log too short to resolve throttle-dependent noise.")

    thr_grid = _resample_uniform(time_s, thr)
    scale = csv_parser.gyro_scale_factor(session.get("headers", {}))
    unfilt_source = session.get("gyro_unfilt_source", "")

    freq = None
    filtered_maps: dict[str, np.ndarray] = {}
    unfiltered_maps: dict[str, np.ndarray] = {}
    for i, axis in enumerate(AXES):
        filt_col = f"gyroADC[{i}]" if f"gyroADC[{i}]" in df.columns else f"gyroData[{i}]"
        if filt_col in df.columns:
            sig = _resample_uniform(time_s, df[filt_col].to_numpy() * scale)
            freq, grid = _spectrogram_by_throttle(sig, thr_grid, fs, nperseg)
            filtered_maps[axis] = _fill_and_smooth(grid)

        unfilt = None
        if unfilt_source == "gyroUnfilt" and f"gyroUnfilt[{i}]" in df.columns:
            unfilt = df[f"gyroUnfilt[{i}]"].to_numpy() * scale
        elif unfilt_source == "debug_gyro_scaled" and f"debug[{i}]" in df.columns:
            unfilt = df[f"debug[{i}]"].to_numpy()  # GYRO_SCALED debug is deg/s
        if unfilt is not None:
            sig = _resample_uniform(time_s, unfilt)
            freq, grid = _spectrogram_by_throttle(sig, thr_grid, fs, nperseg)
            unfiltered_maps[axis] = _fill_and_smooth(grid)

    if freq is None:
        raise NoiseThrottleError("No gyro traces found in this log.")

    grids = {
        "filtered": filtered_maps or None,
        "unfiltered": unfiltered_maps or None,
    }
    freq, grids = _decimate_freq(freq, grids)

    # Shared colour scale: robust max of the noisiest signal above the motion
    # floor. Prefer raw gyro (carries the most energy) so filtered looks calmer.
    fmask = freq >= NOISE_FLOOR_HZ
    ref = grids["unfiltered"] or grids["filtered"]
    band = [m[fmask] for m in ref.values()] if fmask.any() else list(ref.values())
    vmax = float(np.percentile(np.concatenate([b.ravel() for b in band]), 99.5))
    if not (vmax > 0):
        vmax = 1.0

    axes: dict[str, dict] = {}
    for axis in AXES:
        entry = {}
        for key, g in grids.items():
            m = g.get(axis) if g else None
            if m is None:
                entry[key] = None
                continue
            band_vals = m[fmask] if fmask.any() else m
            entry[key] = {
                "grid": m,
                "mean": float(band_vals.mean()),
                "peak": float(band_vals.max()),
            }
        axes[axis] = entry

    throttle = (np.arange(THROTTLE_BINS) + 0.5) * (100.0 / THROTTLE_BINS)
    return {
        "fs": fs,
        "nyquist": fs / 2.0,
        "freq": freq,
        "throttle": throttle,
        "vmax": vmax,
        "unfilt_source": unfilt_source,
        "axes": axes,
    }
