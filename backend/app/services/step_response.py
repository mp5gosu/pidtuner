"""Step-response estimation via windowed Wiener deconvolution.

Algorithm ported from Plasmatree/PID-Analyzer (Beer-ware license, thanks!),
which implements the same approach PIDtoolbox uses: reconstruct the rate-loop
input, deconvolve gyro response per overlapping window, integrate the impulse
response to a step response, and average across windows weighted by a
2D-histogram mode (so the most common response shape wins, not outliers).
"""

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d
from scipy.interpolate import interp1d

from . import csv_parser

FRAMELEN_S = 1.0      # analysis window length
RESPLEN_S = 0.5       # observed response length
SUPERPOS = 16         # overlapping sub-windows per frame
CUTFREQ_HZ = 25.0     # SNR crossover for Wiener regularization
THRESHOLD_DEG_S = 500.0   # split between low/high input rate
TOOLOW_DEG_S = 20.0   # windows below this max input are ignored entirely
P_SCALE = 0.032029    # Betaflight P scaling factor (from PID-Analyzer)

AXES = ("roll", "pitch", "yaw")


class StepResponseError(Exception):
    pass


def _to_mask(clipped: np.ndarray) -> np.ndarray:
    clipped = clipped - clipped.min()
    m = clipped.max()
    return clipped / m if m > 0 else clipped


def _equalize(time: np.ndarray, arrays: list[np.ndarray]):
    """Resample everything onto a uniform time grid."""
    newtime = np.linspace(time[0], time[-1], len(time), dtype=np.float64)
    out = [interp1d(time, a)(newtime) for a in arrays]
    return newtime, out


def _winstacker(arrays: dict[str, np.ndarray], flen: int, superpos: int):
    """Stack overlapping windows: {key: 2D array [n_windows, flen]}."""
    tlen = len(next(iter(arrays.values())))
    shift = int(flen / superpos)
    wins = int(tlen / shift) - superpos
    if wins < 1:
        raise StepResponseError(
            "Log too short for step-response analysis (need > ~2s of flight)."
        )
    idx = np.arange(flen)[None, :] + (np.arange(wins) * shift)[:, None]
    return {k: a[idx] for k, a in arrays.items()}


def _wiener_deconvolution(inp: np.ndarray, outp: np.ndarray, dt: float,
                          cutfreq: float) -> np.ndarray:
    pad = 1024 - (inp.shape[1] % 1024)
    inp = np.pad(inp, [[0, 0], [0, pad]], mode="constant")
    outp = np.pad(outp, [[0, 0], [0, pad]], mode="constant")
    H = np.fft.fft(inp, axis=-1)
    G = np.fft.fft(outp, axis=-1)
    freq = np.abs(np.fft.fftfreq(inp.shape[1], dt))
    sn = _to_mask(np.clip(np.abs(freq), cutfreq - 1e-9, cutfreq))
    len_lpf = np.sum(np.ones_like(sn) - sn)
    sn = _to_mask(gaussian_filter1d(sn, len_lpf / 6.0))
    sn = 10.0 * (-sn + 1.0 + 1e-9)  # +1e-9 avoids 0/0
    Hcon = np.conj(H)
    return np.real(np.fft.ifft(G * Hcon / (H * Hcon + 1.0 / sn), axis=-1))


def _weighted_mode_avr(values: np.ndarray, weights: np.ndarray,
                       time_resp: np.ndarray, vertrange, vertbins: int):
    """Most-common response curve via a weighted 2D histogram."""
    resp_y = np.linspace(vertrange[0], vertrange[-1], vertbins, dtype=np.float64)
    times = np.repeat(np.array([time_resp], dtype=np.float64), len(values), axis=0)
    wts = np.repeat(weights, values.shape[1])

    hist2d = np.histogram2d(
        times.flatten(), values.flatten(),
        range=[[time_resp[0], time_resp[-1]], vertrange],
        bins=[values.shape[1], vertbins], weights=wts,
    )[0].transpose()

    if hist2d.sum():
        hist2d_sm = gaussian_filter1d(hist2d, 7, axis=0, mode="constant")
        colmax = np.max(hist2d_sm, 0)
        colmax[colmax == 0] = 1.0
        hist2d_sm = hist2d_sm / colmax
        pixelpos = np.repeat(resp_y.reshape(len(resp_y), 1), values.shape[1], axis=1)
        avr = np.average(pixelpos, 0, weights=hist2d_sm * hist2d_sm + 1e-12)
    else:
        avr = np.zeros_like(time_resp)
    return avr


def _metrics(time_resp: np.ndarray, resp: np.ndarray) -> dict:
    """Latency / rise time / overshoot from a (roughly settling-to-1) curve."""
    n = len(resp)
    if n < 8 or not np.isfinite(resp).all():
        return {"delay_ms": None, "rise_time_ms": None, "overshoot_pct": None,
                "peak": None, "peak_ms": None}
    final = float(np.mean(resp[int(n * 0.8):]))
    if final <= 0.1:
        return {"delay_ms": None, "rise_time_ms": None, "overshoot_pct": None,
                "peak": None, "peak_ms": None}

    def first_crossing(level):
        above = np.nonzero(resp >= level)[0]
        return float(time_resp[above[0]] * 1000.0) if len(above) else None

    t50 = first_crossing(0.5 * final)
    t10 = first_crossing(0.1 * final)
    t90 = first_crossing(0.9 * final)
    peak_idx = int(np.argmax(resp))
    peak = float(resp[peak_idx])
    return {
        "delay_ms": round(t50, 2) if t50 is not None else None,
        "rise_time_ms": round(t90 - t10, 2) if (t10 is not None and t90 is not None) else None,
        "overshoot_pct": round(max(0.0, (peak - final) / final * 100.0), 1),
        "peak": round(peak, 3),
        "peak_ms": round(float(time_resp[peak_idx] * 1000.0), 2),
    }


def _analyze_axis(time: np.ndarray, gyro: np.ndarray, p_err: np.ndarray,
                  throttle: np.ndarray, p_gain: float, use_setpoint_input: bool,
                  setpoint: np.ndarray | None) -> dict:
    if use_setpoint_input and setpoint is not None:
        loop_input = setpoint
    else:
        loop_input = gyro + p_err / (P_SCALE * p_gain)

    time, (loop_input, gyro, throttle) = _equalize(time, [loop_input, gyro, throttle])
    dt = time[1] - time[0]
    freq = 1.0 / dt
    flen = int(FRAMELEN_S * freq)
    rlen = int(RESPLEN_S * freq)
    time_resp = time[:rlen] - time[0]

    stacks = _winstacker(
        {"input": loop_input, "gyro": gyro, "throttle": throttle}, flen, SUPERPOS
    )
    window = np.hanning(flen)
    inp = stacks["input"] * window
    outp = stacks["gyro"] * window

    deconvolved = _wiener_deconvolution(inp, outp, dt, CUTFREQ_HZ)[:, :rlen]
    resp = deconvolved.cumsum(axis=1)

    max_in = np.max(np.abs(inp), axis=1)
    low_mask = np.where(max_in <= THRESHOLD_DEG_S, 1.0, 0.0)
    high_mask = 1.0 - low_mask
    if high_mask.sum() < 10:
        high_mask *= 0.0
    toolow_mask = np.where(np.abs(max_in) <= TOOLOW_DEG_S, 0.0, 1.0)

    vertrange = [-1.5, 3.5]
    consensus = _weighted_mode_avr(resp, toolow_mask, time_resp, vertrange, 1000)
    low = _weighted_mode_avr(resp, low_mask * toolow_mask, time_resp, vertrange, 1000)
    high = (
        _weighted_mode_avr(resp, high_mask * toolow_mask, time_resp, vertrange, 1000)
        if high_mask.sum() > 0 else None
    )

    return {
        "time_ms": (time_resp * 1000.0),
        "consensus": consensus,
        "low": low,
        "high": high,
        "n_windows": int(toolow_mask.sum()),
        "metrics": _metrics(time_resp, consensus),
    }


def compute(df: pd.DataFrame, session: dict) -> dict:
    headers = session.get("headers", {})
    p_gains = csv_parser.pid_p_gains(headers)
    gyro_scale = csv_parser.gyro_scale_factor(headers)

    time = df["time"].to_numpy() * 1e-6

    if "rcCommand[3]" in df.columns:
        thr_raw = df["rcCommand[3]"].to_numpy()
    elif "setpoint[3]" in df.columns:
        thr_raw = df["setpoint[3]"].to_numpy()
    else:
        thr_raw = np.zeros_like(time)
    try:
        max_thr = float(headers.get("max_throttle", 2000))
    except ValueError:
        max_thr = 2000.0
    throttle = (thr_raw - 1000.0) / (max_thr - 1000.0) * 100.0

    axes = {}
    for i, axis in enumerate(AXES):
        gyro_col = f"gyroADC[{i}]" if f"gyroADC[{i}]" in df.columns else f"gyroData[{i}]"
        if gyro_col not in df.columns:
            axes[axis] = {"error": f"No gyro trace for {axis} in this log."}
            continue
        gyro = df[gyro_col].to_numpy() * gyro_scale

        p_err_col = f"axisP[{i}]"
        setpoint_col = f"setpoint[{i}]"
        setpoint = df[setpoint_col].to_numpy() if setpoint_col in df.columns else None
        p_gain = p_gains[axis]
        has_p = p_err_col in df.columns and p_gain > 0
        use_setpoint = not has_p
        if use_setpoint and setpoint is None:
            axes[axis] = {
                "error": f"Neither axisP[{i}]+PID header nor setpoint[{i}] "
                         "available - cannot reconstruct loop input."
            }
            continue
        p_err = df[p_err_col].to_numpy() if has_p else np.zeros_like(gyro)

        try:
            result = _analyze_axis(
                time, gyro, p_err, throttle, p_gain, use_setpoint, setpoint
            )
            result["input_source"] = "setpoint" if use_setpoint else "p_term_reconstruction"
            result["pid"] = headers.get(f"{axis}_pid", "")
            axes[axis] = result
        except StepResponseError as e:
            axes[axis] = {"error": str(e)}

    return {"axes": axes}
