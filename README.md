# PIDtuner

A lean, browser-based PIDtoolbox clone: upload Betaflight Blackbox logs,
compare filtered/unfiltered gyro, inspect the gyro noise spectrum, see how
noise varies with throttle, and analyze the step response of the rate
controller with latency metrics.

- **Backend**: FastAPI + numpy/scipy/pandas, decodes logs with the
  official [`blackbox_decode`](https://github.com/betaflight/blackbox-tools)
- **Frontend**: Vanilla JS + [uPlot](https://github.com/leeoniya/uPlot),
  no build step. All charts: scroll = zoom X, Shift+scroll = zoom Y,
  drag = pan, double-click = reset. The three axis charts are
  synchronized on the time axis.
- **Noise spectrum tab**: per-axis amplitude spectrum (Welch's method)
  of the filtered and unfiltered gyro, for hunting noise sources and
  setting the Betaflight gyro lowpass / dynamic notch / RPM filters. Each
  axis chart carries its own independent set of frequency markers (not
  synced across axes): add as many as you like ("+ Marker"), drag each
  onto a peak to read its frequency, double-click it to set a label, and
  remove them one at a time (×) or all at once ("Clear"). Every marker
  gets its own color. The Y axis auto-frames to the noise band above
  ~30 Hz, since low-frequency craft motion is orders of magnitude larger —
  zoom out (Shift+scroll) to see it.
- **Noise vs. Throttle tab**: a throttle&nbsp;×&nbsp;frequency spectrogram of
  the gyro per axis (PIDtoolbox's "throttle x frequency" view). Each short-time
  spectrum is binned by the throttle it was recorded at, so throttle-dependent
  noise stands out — motor/frame resonances that ramp with RPM appear as
  diagonal ridges, telling you *which throttle band* a peak lives in. Switch
  the shared control between raw and filtered gyro (both share one colour scale,
  so the filter's effect is obvious), drag the scale slider to set the colour
  ceiling, toggle "&lt;&nbsp;100&nbsp;Hz" to zoom the low band, and hover any
  heatmap to read throttle&nbsp;·&nbsp;Hz&nbsp;·&nbsp;amplitude. Throttle is
  taken from `rcCommand[3]` (`(v-1000)/10` → 0–100&nbsp;%), falling back to
  `setpoint[3]`. Rendered on a plain canvas (uPlot has no heatmap).
- **Step response tab**: overlays the consensus curves of up to 8
  sessions, with per-axis latency bar charts and a metrics table (PID,
  latency, rise, peak, overshoot). When two or more measurable sessions
  are compared, a ❤️ marks the best all-round configuration — the one
  with the lowest combined latency, rise time and overshoot (each metric
  min-max normalized across the compared sessions, summed over the axes
  they share). After upload, the longest session is displayed
  automatically. Storage is fully ephemeral and per-user: each upload is
  isolated (never shared/deduplicated between users) and the server wipes its
  work directory on startup. Expiry is **inactivity-based**: a reaper prunes any
  log left untouched for `PIDTUNER_DATA_TTL_MIN` (default 60), while every fetch
  and a background keepalive heartbeat from open browser tabs refresh it. The
  browser also remembers its uploads locally and re-attaches on load, so an
  accidental page reload is not a total loss (there is deliberately no manual
  delete button). Step-response results are cached per session
  (`stepresp_v*_<sid>.json`) for the lifetime of that upload; bump the version
  tag when changing the algorithm.

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

## Docker

A multi-stage [`Dockerfile`](Dockerfile) builds `blackbox_decode` from source
and bakes it into a slim Python runtime together with the backend and frontend —
no `uv`, `gcc` or binary juggling on the host. The container serves on port
`8000`.

```bash
docker compose up -d          # build + run, http://localhost:8000
docker compose logs -f        # follow logs
docker compose down           # stop & remove
```

Storage is **ephemeral**: uploaded logs are decoded into a scratch dir inside
the container, wiped on startup and when the container is removed, isolated
per user (no cross-user dedup), and reaped after `PIDTUNER_DATA_TTL_MIN` of
inactivity (open tabs stay alive via a keepalive heartbeat; a reload re-attaches).
There is no persistent volume — a fresh start is always clean, which is what you
want when several people upload logs to the same instance.

The image runs uvicorn with **one worker** (the caches are process-local).
Configure via environment variables in [`docker-compose.yml`](docker-compose.yml)
(`PIDTUNER_DATA_TTL_MIN`, `PIDTUNER_REAP_INTERVAL_MIN`, `PIDTUNER_MAX_UPLOAD_BYTES`, …).

To build a plain image without compose:

```bash
docker build -t pidtuner:latest .
docker run -d -p 8000:8000 pidtuner:latest
```

> The image build clones `betaflight/blackbox-tools` from GitHub, so it needs
> network access **at build time**; the running container is fully offline.

### Prebuilt image (GitHub Actions → ghcr.io)

[`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml)
builds and pushes the image to the GitHub Container Registry on every push to
`develop`/`main`, on `v*` tags, and on manual dispatch — no secrets to set up
(it uses the built-in `GITHUB_TOKEN`). To pull instead of building, activate the
`image:` line in [`docker-compose.yml`](docker-compose.yml) (and remove
`build: .`):

```
ghcr.io/mp5gosu/pidtuner:latest      # tracks the default branch
ghcr.io/mp5gosu/pidtuner:v1.2.3      # from a `git tag v1.2.3`
```

Packages are private by default; make the package public (or `docker login
ghcr.io` with a PAT) to pull without credentials.

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
  api/routes_logs.py         POST /api/logs (upload), sessions, rename, keepalive, DELETE
  api/routes_gyro.py         GET .../gyro (binary format, min/max-decimated)
  api/routes_spectrum.py     GET .../spectrum (JSON, Welch amplitude spectrum)
  api/routes_noise_throttle.py GET .../noise-throttle (binary, freq x throttle maps)
  api/routes_step_response.py GET .../step-response (JSON)
  services/blackbox_decoder.py  session split + blackbox_decode subprocess
  services/csv_parser.py     .bbl header ("H ..." lines) + CSV load + gyro_scale
  services/gyro_series.py    filtered/unfiltered per axis + decimation
  services/spectrum.py       per-axis Welch amplitude spectrum (filtered/unfilt)
  services/noise_throttle.py per-axis STFT spectrogram binned by throttle
  services/step_response.py  Wiener deconvolution (port of PID-Analyzer)
  services/session_store.py  log registry + DataFrame LRU cache + inactivity reaper (last_access)
  core/binary_pack.py        typed-array binary format for large time series
frontend/
  js/charts/zoomPlugin.js    wheel zoom/drag pan/reset + X-sync group
  js/charts/gyroChart.js     3x uPlot, switchable signals (gyro raw/
                             filtered, setpoint, error, P/I/D term)
  js/charts/spectrumChart.js 3x uPlot + shared draggable harmonic marker
  js/charts/noiseThrottleChart.js 3x canvas heatmap + signal/scale/band controls
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
