"""Extract filtered/unfiltered gyro time series for charting."""

import numpy as np
import pandas as pd

from .. import config
from . import csv_parser

AXES = ("roll", "pitch", "yaw")


def minmax_decimate(time: np.ndarray, series: list[np.ndarray], max_points: int):
    """Bucket-wise min/max decimation preserving the noise envelope.

    All series share the same buckets and time axis. Each bucket contributes
    two points per series (its min and its max), so spikes survive. Exact
    positions within a bucket are approximated by bucket start/middle, which
    is visually indistinguishable at the zoom levels where decimation matters.
    """
    n = len(time)
    if n <= max_points:
        return time, series

    n_buckets = max(1, max_points // 2)
    bucket = n // n_buckets
    usable = n_buckets * bucket

    t = time[:usable].reshape(n_buckets, bucket)
    out_time = np.empty(n_buckets * 2, dtype=time.dtype)
    out_time[0::2] = t[:, 0]
    out_time[1::2] = t[:, bucket // 2] if bucket > 1 else t[:, 0]

    outs = []
    for s in series:
        chunk = s[:usable].reshape(n_buckets, bucket)
        out = np.empty(n_buckets * 2, dtype=s.dtype)
        out[0::2] = chunk.min(axis=1)
        out[1::2] = chunk.max(axis=1)
        outs.append(out)
    return out_time, outs


def extract(df: pd.DataFrame, session: dict, max_points: int | None = None) -> dict:
    """Build per-axis signal series for the gyro view.

    Emits every signal that exists in the log so the frontend can toggle them:
    filtered/unfiltered gyro (deg/s), setpoint (deg/s), tracking error
    (setpoint - filtered gyro, deg/s) and the P/I/D term contributions (raw
    controller units). Absent signals are simply omitted per axis (yaw, for
    instance, usually has no D term).

    Returns {'axes': {axis: {'time_s': nd, <signal>: nd, ...}},
    'unfilt_source': str}. All signals of one axis share the same decimation
    buckets and time axis.
    """
    max_points = max_points or config.MAX_CHART_POINTS
    time_us = df["time"].to_numpy()
    time_s = (time_us - time_us[0]) * 1e-6
    unfilt_source = session.get("gyro_unfilt_source", "")
    scale = csv_parser.gyro_scale_factor(session.get("headers", {}))

    def col(name: str):
        return df[name].to_numpy() if name in df.columns else None

    axes = {}
    for i, axis in enumerate(AXES):
        filt_col = f"gyroADC[{i}]" if f"gyroADC[{i}]" in df.columns else f"gyroData[{i}]"
        filtered = (df[filt_col].to_numpy() * scale) if filt_col in df.columns \
            else np.zeros_like(time_s)

        unfiltered = None
        if unfilt_source == "gyroUnfilt" and f"gyroUnfilt[{i}]" in df.columns:
            unfiltered = df[f"gyroUnfilt[{i}]"].to_numpy() * scale
        elif unfilt_source == "debug_gyro_scaled" and f"debug[{i}]" in df.columns:
            # GYRO_SCALED debug values are already deg/s
            unfiltered = df[f"debug[{i}]"].to_numpy()

        setpoint = col(f"setpoint[{i}]")
        p_term = col(f"axisP[{i}]")
        i_term = col(f"axisI[{i}]")
        d_term = col(f"axisD[{i}]")
        # tracking error the controller acts on (deg/s); only meaningful when
        # a setpoint was logged.
        error = (setpoint - filtered) if setpoint is not None else None

        # stable key order -> stable uPlot series index order on the frontend
        candidates = [
            ("filtered", filtered),
            ("unfiltered", unfiltered),
            ("setpoint", setpoint),
            ("error", error),
            ("p", p_term),
            ("i", i_term),
            ("d", d_term),
        ]
        present = [(k, a) for k, a in candidates if a is not None]

        arrays = [a for _, a in present]
        dec_time, dec = minmax_decimate(time_s, arrays, max_points)
        entry = {"time_s": dec_time}
        for (k, _), a in zip(present, dec):
            entry[k] = a
        axes[axis] = entry

    return {"axes": axes, "unfilt_source": unfilt_source}
