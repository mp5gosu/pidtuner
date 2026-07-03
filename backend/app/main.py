import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import routes_gyro, routes_logs, routes_spectrum, routes_step_response
from .services import blackbox_decoder, session_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    blackbox_decoder.check_binary()
    log.info("blackbox_decode OK: %s", config.BLACKBOX_DECODE_BIN)
    removed = session_store.cleanup_expired()
    if removed:
        log.info("Removed %d expired cached log(s)", removed)
    yield


app = FastAPI(title="PIDtuner", lifespan=lifespan)

app.include_router(routes_logs.router)
app.include_router(routes_gyro.router)
app.include_router(routes_spectrum.router)
app.include_router(routes_step_response.router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
