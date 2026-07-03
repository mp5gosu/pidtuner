# PIDtuner

A lean, browser-based PIDtoolbox clone: upload Betaflight Blackbox logs,
compare filtered/unfiltered gyro, and analyze the step response of the
rate controller with latency metrics.

- **Backend**: FastAPI + numpy/scipy/pandas, decodes logs with the
  official [`blackbox_decode`](https://github.com/betaflight/blackbox-tools)
- **Frontend**: Vanilla JS + [uPlot](https://github.com/leeoniya/uPlot),
  no build step. All charts: scroll = zoom X, Shift+scroll = zoom Y,
  drag = pan, double-click = reset. The three axis charts are
  synchronized on the time axis.
- **Step response tab**: overlays the consensus curves of up to 8
  sessions, with per-axis latency bar charts and a metrics table (PID,
  latency, rise, peak, overshoot). After upload, the longest session is
  displayed automatically. Only the logs of the current browser session
  are visible; on the server side, uploads remain as an invisible
  decode/dedup cache (which makes duplicate uploads finish instantly)
  and are cleaned up on startup after `PIDTUNER_DATA_TTL_DAYS`
  (default 14). Step-response results are cached as
  `stepresp_v*_<sid>.json`; bump the version tag when changing the
  algorithm.

## Setup

Prerequisites: `git`, `make`, `gcc`, [`uv`](https://docs.astral.sh/uv/)

```bash
./scripts/build_blackbox_tools.sh    # builds backend/bin/blackbox_decode
cd backend
uv sync
uv run uvicorn app.main:app          # http://localhost:8000
```

> **Only 1 worker!** The session cache is process-local;
> `--workers > 1` would break it. For a local single-user tool
> this is the correct configuration.

If building is not possible (e.g. Windows without WSL), set the path to an
existing binary, for example from a Betaflight Configurator installation:

```bash
export PIDTUNER_BLACKBOX_DECODE_BIN=/path/to/blackbox_decode
```

## Important: unfiltered gyro requires the right log configuration

The filtered/unfiltered comparison only works if the log contains
unfiltered gyro data. That is the case when:

- the firmware logs `gyroUnfilt[]` (Betaflight ≥ 4.3 with the
  corresponding `blackbox_disable_*` defaults), **or**
- `set debug_mode = GYRO_SCALED` + `save` was set in the Betaflight CLI
  before recording (then the data is in `debug[0..2]`).

If neither is present, the app shows a notice banner and only the
filtered signal. This is a property of the log and cannot be fixed
after the fact.

## Architecture

```
backend/app/
  main.py                    FastAPI app, startup check, static serving
  config.py                  paths/limits, overridable via PIDTUNER_* env vars
  api/routes_logs.py         POST /api/logs (upload), sessions, DELETE
  api/routes_gyro.py         GET .../gyro (binary format, min/max-decimated)
  api/routes_step_response.py GET .../step-response (JSON)
  services/blackbox_decoder.py  session split + blackbox_decode subprocess
  services/csv_parser.py     .bbl header ("H ..." lines) + CSV load + gyro_scale
  services/gyro_series.py    filtered/unfiltered per axis + decimation
  services/step_response.py  Wiener deconvolution (port of PID-Analyzer)
  services/session_store.py  log registry + DataFrame LRU cache
  core/binary_pack.py        typed-array binary format for large time series
frontend/
  js/charts/zoomPlugin.js    wheel zoom/drag pan/reset + X-sync group
  js/charts/gyroChart.js     3x uPlot, switchable signals (gyro raw/
                             filtered, setpoint, error, P/I/D term)
  js/charts/stepResponseChart.js  3x uPlot + latency/rise/overshoot
```

### Step-response algorithm

Port of [Plasmatree/PID-Analyzer](https://github.com/Plasmatree/PID-Analyzer)
(Beer-ware license — thank you!), the same method that PIDtoolbox uses:

1. Reconstruct the loop input: `input = gyro + axisP/(0.032029 · P)`
   (fallback: `setpoint[]` if the axisP/PID header is missing)
2. Overlapping 1-s windows (16-fold overlapped, Hanning window)
3. Wiener deconvolution in the frequency domain (regularization above 25 Hz)
4. Impulse response → `cumsum` → step response (0–500 ms)
5. Discard windows with too little stick input (< 20 °/s), average the
   rest into the consensus curve via a weighted 2D histogram mode,
   additionally split by input rates < / > 500 °/s
6. Metrics: latency (50% crossing), rise time (10→90%), peak, overshoot

**Gyro scaling**: rotation is deliberately decoded in raw units and the
`gyro_scale` from the log header is applied in Python. Reason:
blackbox-tools does not recognize the header `Firmware type:Betaflight`
(new as of BF 2025.12) and would apply the wrong Baseflight scaling with
`--unit-rotation deg/s` (~5.7·10⁷ times too large) — the step response
then degenerates into a flat line at 1.0.

## Tests

```bash
cd backend
PYTHONPATH=. uv run python tests/test_step_response.py   # numerical validation
uv run python tests/make_synthetic_bbl.py /tmp/synthetic.bbl  # generate test log
```

`make_synthetic_bbl.py` generates a real Blackbox v2 log with a known
system response (8 ms delay + 8 ms ramp) — the step-response view must
then show ~12 ms latency.
</content>
</invoke>
