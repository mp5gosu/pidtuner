"""Numerical sanity check: feed a synthetic system with known delay through
the deconvolution and verify the recovered step response."""

import numpy as np
import pandas as pd

from app.services import step_response


def make_synthetic_df(delay_s=0.010, ramp_s=0.010, duration_s=20.0, freq=4000.0,
                      noise=5.0, seed=42):
    rng = np.random.default_rng(seed)
    n = int(duration_s * freq)
    t = np.arange(n) / freq

    # band-limited random stick input (~30Hz bandwidth), scaled to +-300 deg/s
    raw = rng.standard_normal(n)
    from scipy.ndimage import gaussian_filter1d
    setpoint = gaussian_filter1d(raw, freq * 0.005)
    setpoint *= 300.0 / np.abs(setpoint).max()

    # known system: pure delay + short ramp (like PID-Analyzer's toy_out)
    kernel = np.zeros(int((delay_s + ramp_s) * freq))
    kernel[int(delay_s * freq):] = 1.0
    kernel /= kernel.sum()
    gyro = np.convolve(setpoint, kernel, mode="full")[:n]
    gyro += (rng.random(n) - 0.5) * noise

    return pd.DataFrame({
        "time": t * 1e6,
        "gyroADC[0]": gyro, "gyroADC[1]": gyro, "gyroADC[2]": gyro,
        "setpoint[0]": setpoint, "setpoint[1]": setpoint, "setpoint[2]": setpoint,
        "rcCommand[3]": np.full(n, 1600.0),
    })


def test_recovers_known_delay():
    delay_s = 0.010
    df = make_synthetic_df(delay_s=delay_s)
    session = {"headers": {"max_throttle": "2000"}}  # no PID header -> setpoint input

    result = step_response.compute(df, session)
    roll = result["axes"]["roll"]

    assert "error" not in roll, roll.get("error")
    assert roll["input_source"] == "setpoint"

    m = roll["metrics"]
    assert m["delay_ms"] is not None
    # delay (50% crossing) should be ~ delay + half ramp = 15ms, generous tolerance
    assert 8.0 < m["delay_ms"] < 25.0, m

    # steady state should settle near 1.0
    consensus = np.array(roll["consensus"])
    tail = consensus[int(len(consensus) * 0.6):]
    assert 0.85 < tail.mean() < 1.15, tail.mean()

    print("metrics:", m)
    print("steady state:", round(float(tail.mean()), 3))


def test_too_short_log_raises_cleanly():
    df = make_synthetic_df(duration_s=0.5)
    session = {"headers": {}}
    result = step_response.compute(df, session)
    assert "error" in result["axes"]["roll"]


if __name__ == "__main__":
    test_recovers_known_delay()
    test_too_short_log_raises_cleanly()
    print("all step-response tests passed")
