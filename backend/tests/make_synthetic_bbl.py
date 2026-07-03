"""Generate a synthetic Betaflight blackbox .bbl for end-to-end testing.

Writes a valid Blackbox v2 stream using only I-frames (predictor 0,
UVB/SVB encodings), so `blackbox_decode` can decode it. The simulated
"quad" is a known system (pure delay + ramp), so the step-response tab
should recover ~delay+ramp/2 latency, and gyroUnfilt carries added HF
noise so the filtered/unfiltered comparison is visible.

Usage: python tests/make_synthetic_bbl.py [out.bbl] [--sessions N] [--no-unfilt]
"""

import struct
import sys

import numpy as np
from scipy.ndimage import gaussian_filter1d

FREQ = 2000.0          # log rate Hz
DURATION_S = 25.0
DELAY_S = 0.008        # known system delay
RAMP_S = 0.008         # known system ramp
P_GAINS = {"roll": 45.0, "pitch": 47.0, "yaw": 30.0}
P_SCALE = 0.032029

FIELDS = [
    # (name, signed)
    ("loopIteration", 0),
    ("time", 0),
    ("axisP[0]", 1), ("axisP[1]", 1), ("axisP[2]", 1),
    ("rcCommand[0]", 1), ("rcCommand[1]", 1), ("rcCommand[2]", 1), ("rcCommand[3]", 0),
    ("setpoint[0]", 1), ("setpoint[1]", 1), ("setpoint[2]", 1), ("setpoint[3]", 0),
    ("gyroADC[0]", 1), ("gyroADC[1]", 1), ("gyroADC[2]", 1),
    ("gyroUnfilt[0]", 1), ("gyroUnfilt[1]", 1), ("gyroUnfilt[2]", 1),
    ("debug[0]", 1), ("debug[1]", 1), ("debug[2]", 1), ("debug[3]", 1),
]


def write_uvb(buf: bytearray, value: int):
    value &= 0xFFFFFFFF
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            return


def write_svb(buf: bytearray, value: int):
    write_uvb(buf, ((value << 1) ^ (value >> 31)) & 0xFFFFFFFF)


def headers(with_unfilt: bool) -> bytes:
    fields = FIELDS if with_unfilt else [f for f in FIELDS if "Unfilt" not in f[0]]
    names = ",".join(f[0] for f in fields)
    signed = ",".join(str(f[1]) for f in fields)
    predictors = ",".join("0" for _ in fields)
    encodings = ",".join("0" if f[1] else "1" for f in fields)  # SVB / UVB
    gyro_scale_hex = "0x" + struct.pack(">f", 1.0).hex()

    lines = [
        "H Product:Blackbox flight data recorder by Nicholas Sherlock",
        "H Data version:2",
        "H I interval:32",
        "H P interval:1/32",
        f"H Field I name:{names}",
        f"H Field I signed:{signed}",
        f"H Field I predictor:{predictors}",
        f"H Field I encoding:{encodings}",
        f"H Field P predictor:{predictors}",
        f"H Field P encoding:{encodings}",
        "H Firmware type:Cleanflight",
        "H Firmware revision:Betaflight 4.4.2 (synthetic)",
        "H Craft name:SynthQuad",
        f"H gyro_scale:{gyro_scale_hex}",
        "H looptime:125",
        "H pid_process_denom:4",
        "H rollPID:45,80,35",
        "H pitchPID:47,84,38",
        "H yawPID:30,90,0",
        "H minthrottle:1070",
        "H maxthrottle:2000",
        "H debug_mode:0",
        "H gyro_lowpass_hz:250",
        "H dterm_lpf_hz:150",
    ]
    return ("\n".join(lines) + "\n").encode("latin-1")


def make_session_data(seed: int, with_unfilt: bool) -> bytes:
    rng = np.random.default_rng(seed)
    n = int(DURATION_S * FREQ)
    t_us = (np.arange(n) * (1e6 / FREQ)).astype(np.int64)
    t_s = np.arange(n) / FREQ

    # per-axis band-limited stick input, +-400 deg/s
    setpoints, gyros, unfilts, axis_ps = [], [], [], []
    kernel = np.zeros(int((DELAY_S + RAMP_S) * FREQ))
    kernel[int(DELAY_S * FREQ):] = 1.0
    kernel /= kernel.sum()

    for ai, axis in enumerate(("roll", "pitch", "yaw")):
        sp = gaussian_filter1d(rng.standard_normal(n), FREQ * 0.005)
        sp *= (400.0 - 80.0 * ai) / np.abs(sp).max()
        gy = np.convolve(sp, kernel, mode="full")[:n]
        # unfiltered = filtered + motor noise (HF sines + white)
        noise = (
            8.0 * np.sin(2 * np.pi * 180.0 * t_s + ai)
            + 5.0 * np.sin(2 * np.pi * 320.0 * t_s)
            + rng.normal(0, 4.0, n)
        )
        p_err = sp - gy
        axis_p = P_SCALE * P_GAINS[axis] * p_err

        setpoints.append(np.round(sp).astype(np.int64))
        gyros.append(np.round(gy).astype(np.int64))
        unfilts.append(np.round(gy + noise).astype(np.int64))
        axis_ps.append(np.round(axis_p).astype(np.int64))

    throttle = 1500 + 150 * np.sin(2 * np.pi * 0.15 * t_s)
    throttle = np.round(throttle).astype(np.int64)

    buf = bytearray()
    for i in range(n):
        buf.append(ord("I"))
        write_uvb(buf, i)                       # loopIteration
        write_uvb(buf, int(t_us[i]))            # time
        for ai in range(3):
            write_svb(buf, int(axis_ps[ai][i]))
        for ai in range(3):
            write_svb(buf, int(setpoints[ai][i]))  # rcCommand[0..2] ~ setpoint
        write_uvb(buf, int(throttle[i]))        # rcCommand[3]
        for ai in range(3):
            write_svb(buf, int(setpoints[ai][i]))
        write_uvb(buf, int(throttle[i]))        # setpoint[3]
        for ai in range(3):
            write_svb(buf, int(gyros[ai][i]))
        if with_unfilt:
            for ai in range(3):
                write_svb(buf, int(unfilts[ai][i]))
        for _ in range(4):
            write_svb(buf, 0)                   # debug[0..3]
    return bytes(buf)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "synthetic.bbl"
    n_sessions = 2
    if "--sessions" in sys.argv:
        n_sessions = int(sys.argv[sys.argv.index("--sessions") + 1])
    with_unfilt = "--no-unfilt" not in sys.argv

    blob = b""
    for s in range(n_sessions):
        blob += headers(with_unfilt) + make_session_data(seed=100 + s, with_unfilt=with_unfilt)
    with open(out, "wb") as f:
        f.write(blob)
    print(f"wrote {out}: {n_sessions} session(s), {len(blob)/1e6:.1f} MB, "
          f"unfilt={'yes' if with_unfilt else 'no'}")


if __name__ == "__main__":
    main()
