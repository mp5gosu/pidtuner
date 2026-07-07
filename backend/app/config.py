import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

BLACKBOX_DECODE_BIN = Path(
    os.environ.get("PIDTUNER_BLACKBOX_DECODE_BIN", BACKEND_DIR / "bin" / "blackbox_decode")
)
DATA_DIR = Path(os.environ.get("PIDTUNER_DATA_DIR", BACKEND_DIR / "data"))
FRONTEND_DIR = Path(os.environ.get("PIDTUNER_FRONTEND_DIR", PROJECT_DIR / "frontend"))

MAX_UPLOAD_BYTES = int(os.environ.get("PIDTUNER_MAX_UPLOAD_BYTES", 512 * 1024 * 1024))

# Sessions smaller than this are considered bogus arm/disarm blips and skipped.
MIN_SESSION_BYTES = int(os.environ.get("PIDTUNER_MIN_SESSION_BYTES", 64 * 1024))

DECODE_TIMEOUT_S = int(os.environ.get("PIDTUNER_DECODE_TIMEOUT_S", 180))

# Cap for time-series points sent to the browser per series (min/max decimated).
MAX_CHART_POINTS = int(os.environ.get("PIDTUNER_MAX_CHART_POINTS", 400_000))

# Server-side upload storage is fully ephemeral: it is wiped on startup and a
# periodic reaper deletes any log untouched for DATA_TTL_MIN (inactivity, not
# age — every fetch/heartbeat refreshes it), so an open browser tab keeps its
# uploads alive and an accidental reload is not a total loss. 0 disables the
# reaper. Uploads are never shared between users (no dedup).
DATA_TTL_MIN = float(os.environ.get("PIDTUNER_DATA_TTL_MIN", 60))

# How often the background reaper runs (minutes). 0 disables it.
REAP_INTERVAL_MIN = float(os.environ.get("PIDTUNER_REAP_INTERVAL_MIN", 10))
