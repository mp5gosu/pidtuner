import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import (
    routes_gyro,
    routes_logs,
    routes_noise_throttle,
    routes_spectrum,
    routes_step_response,
)
from .services import blackbox_decoder, session_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def _reaper():
    """Prune uploads left inactive for DATA_TTL_MIN. Every fetch and the browser
    keepalive heartbeat refresh a log's last_access, so only genuinely-abandoned
    uploads are reaped; a fresh startup also wipes everything."""
    interval = config.REAP_INTERVAL_MIN * 60
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            removed = session_store.cleanup_expired()
            if removed:
                log.info("Reaper removed %d abandoned upload(s)", removed)
        except Exception:
            log.exception("Reaper pass failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Storage is ephemeral: start from a clean slate every launch.
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    session_store.wipe_all()
    blackbox_decoder.check_binary()
    log.info("blackbox_decode OK: %s", config.BLACKBOX_DECODE_BIN)
    task = asyncio.create_task(_reaper())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        session_store.wipe_all()


app = FastAPI(title="PIDtuner", lifespan=lifespan)

app.include_router(routes_logs.router)
app.include_router(routes_gyro.router)
app.include_router(routes_spectrum.router)
app.include_router(routes_noise_throttle.router)
app.include_router(routes_step_response.router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
