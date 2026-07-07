"""Parse blackbox headers (from the raw .bbl) and decoded CSV data."""

import re
import struct
from pathlib import Path

import numpy as np
import pandas as pd

# Header keys we care about, with legacy aliases (see PID-Analyzer's beheader()).
_HEADER_KEYS = {
    "Craft name": "craft_name",
    "Firmware type": "fw_type",
    "Firmware revision": "fw_revision",
    "rollPID": "roll_pid",
    "pitchPID": "pitch_pid",
    "yawPID": "yaw_pid",
    "maxthrottle": "max_throttle",
    "minthrottle": "min_throttle",
    "debug_mode": "debug_mode",
    "looptime": "looptime",
    "pid_process_denom": "pid_process_denom",
    "gyro_scale": "gyro_scale",
    "gyro.scale": "gyro_scale",
    "gyro_lowpass_hz": "gyro_lowpass_hz",
    "gyro_lpf_hz": "gyro_lowpass_hz",
    "dterm_lpf_hz": "dterm_lpf_hz",
}

# Betaflight debug_mode index for GYRO_SCALED (unfiltered gyro in debug[0..2]).
# Stable at 6 for BF 4.x. Header may also contain the name instead of a number.
GYRO_SCALED_INDEX = "6"


def read_headers(bbl_path: Path) -> dict:
    """Extract 'H name:value' headers from a single-session .bbl file."""
    headers = {}
    with open(bbl_path, "rb") as f:
        raw = f.read(65536)
    for line in raw.split(b"\n"):
        if not line.startswith(b"H "):
            continue
        try:
            text = line[2:].decode("latin-1").strip()
        except UnicodeDecodeError:
            continue
        if ":" not in text:
            continue
        key, _, value = text.partition(":")
        key = key.strip()
        for known, target in _HEADER_KEYS.items():
            if key == known:
                headers[target] = value.strip()
    return headers


def read_raw_headers(bbl_path: Path) -> dict:
    """Extract *all* 'H name:value' header pairs, keys as written in the log.

    Unlike read_headers (which normalizes an allow-list for the decode/analysis
    services), this keeps every tuning field Betaflight logged - PID gains,
    feedforward, rates, simplified-tuning sliders, filters, TPA, etc. - keyed
    by the raw header name (e.g. 'rollPID', 'Craft name', 'simplified_master_multiplier').
    Used purely for display; the frontend curates which fields it surfaces.
    """
    headers = {}
    with open(bbl_path, "rb") as f:
        raw = f.read(65536)
    for line in raw.split(b"\n"):
        if not line.startswith(b"H "):
            continue
        try:
            text = line[2:].decode("latin-1").strip()
        except UnicodeDecodeError:
            continue
        if ":" not in text:
            continue
        key, _, value = text.partition(":")
        headers[key.strip()] = value.strip()
    return headers


def _normalize_column(name: str) -> str:
    """'gyroADC[0] (deg/s)' -> 'gyroADC[0]'; strips unit suffix + whitespace."""
    return re.sub(r"\s*\(.*\)\s*$", "", name.strip())


_WANTED_PREFIXES = (
    "time",
    "gyroADC[", "gyroUnfilt[", "gyroData[", "ugyroADC[",
    "setpoint[", "rcCommand[",
    "axisP[", "axisI[", "axisD[",
    "debug[",
)


def load_dataframe(csv_path: Path) -> pd.DataFrame:
    """Load the decoded CSV with normalized column names, restricted columns,
    float32 values (float64 for time)."""
    with open(csv_path) as f:
        header_line = f.readline()
    raw_cols = [c.strip() for c in header_line.split(",")]
    usecols = [
        c for c in raw_cols
        if _normalize_column(c).startswith(_WANTED_PREFIXES)
    ]
    # float32 for the bulk (halves the cached-frame RAM; the wire format
    # down-converts to f32 anyway), float64 only for the microsecond time base
    # where the large absolute magnitude needs the extra precision.
    dtypes = {
        c: (np.float64 if _normalize_column(c) == "time" else np.float32)
        for c in usecols
    }
    df = pd.read_csv(
        csv_path,
        skipinitialspace=True,
        usecols=usecols,
        dtype=dtypes,
        on_bad_lines="skip",
    )
    df.columns = [_normalize_column(c) for c in df.columns]
    return df


def quick_duration_s(csv_path: Path) -> float:
    """Duration estimate from first and last data row without a full parse."""
    with open(csv_path, "rb") as f:
        header = f.readline().decode("latin-1")
        cols = [_normalize_column(c) for c in header.split(",")]
        try:
            time_idx = cols.index("time")
        except ValueError:
            return 0.0
        first = f.readline().decode("latin-1", errors="replace")
        # last line: read tail chunk
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 8192))
        tail = f.read().decode("latin-1", errors="replace").strip().splitlines()
    try:
        t0 = float(first.split(",")[time_idx])
        t1 = float(tail[-1].split(",")[time_idx])
        return max(0.0, (t1 - t0) * 1e-6)
    except (ValueError, IndexError):
        return 0.0


def quick_sample_rate_hz(csv_path: Path) -> float:
    """Logging sample rate estimate from the median gap of the first data rows.

    Cheap: reads only the header + a handful of rows (like quick_duration_s).
    Time is decoded in microseconds (blackbox_decode --unit-frame-time us), so
    the rate is 1e6 / median(Δtime). Returns 0.0 if it can't be determined.
    """
    with open(csv_path, "rb") as f:
        header = f.readline().decode("latin-1")
        cols = [_normalize_column(c) for c in header.split(",")]
        try:
            time_idx = cols.index("time")
        except ValueError:
            return 0.0
        times = []
        for _ in range(16):
            line = f.readline()
            if not line:
                break
            try:
                times.append(float(line.decode("latin-1", errors="replace").split(",")[time_idx]))
            except (ValueError, IndexError):
                continue
    dts = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    if not dts:
        return 0.0
    median = dts[len(dts) // 2]
    return 1e6 / median if median > 0 else 0.0


def unfilt_availability(csv_path: Path, headers: dict) -> tuple[bool, str]:
    """Determine whether unfiltered gyro data exists and where it comes from.

    Returns (available, source) with source in {'gyroUnfilt', 'debug_gyro_scaled', ''}.
    """
    with open(csv_path) as f:
        cols = {_normalize_column(c) for c in f.readline().split(",")}
    if "gyroUnfilt[0]" in cols:
        return True, "gyroUnfilt"
    debug_mode = headers.get("debug_mode", "")
    if debug_mode == GYRO_SCALED_INDEX or debug_mode.upper() == "GYRO_SCALED":
        if "debug[0]" in cols:
            return True, "debug_gyro_scaled"
    return False, ""


def gyro_scale_factor(headers: dict) -> float:
    """deg/s per raw gyro LSB, from the 'gyro_scale' header (hex float).

    Modern Betaflight logs gyro in deg/s directly (scale 1.0); older
    Cleanflight logs carry the real LSB scale here. We decode rotation in
    raw units (see blackbox_decoder) and apply this factor ourselves.
    """
    raw = headers.get("gyro_scale", "")
    try:
        if raw.startswith("0x"):
            scale = struct.unpack(">f", bytes.fromhex(raw[2:].zfill(8)))[0]
        else:
            scale = float(raw)
    except (ValueError, struct.error):
        return 1.0
    return scale if 0.0 < scale <= 10.0 else 1.0


def pid_p_gains(headers: dict) -> dict[str, float]:
    """P gain per axis from 'rollPID: 45,80,40'-style headers (0.0 if absent)."""
    gains = {}
    for axis, key in (("roll", "roll_pid"), ("pitch", "pitch_pid"), ("yaw", "yaw_pid")):
        try:
            gains[axis] = float(headers[key].split(",")[0])
        except (KeyError, ValueError, IndexError):
            gains[axis] = 0.0
    return gains
