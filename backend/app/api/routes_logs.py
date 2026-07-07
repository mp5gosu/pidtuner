import logging
import shutil

from fastapi import APIRouter, Body, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from .. import config
from ..services import blackbox_decoder, session_store

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/logs")
async def upload_log(request: Request, file: UploadFile):
    length = request.headers.get("content-length")
    # Fast-path reject; a malformed header just falls through to the streaming
    # cap below, which enforces the real limit.
    try:
        if length and int(length) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Log file too large")
    except ValueError:
        pass

    log_id = session_store.create_log_id()
    log_dir = config.DATA_DIR / log_id
    log_dir.mkdir(parents=True, exist_ok=True)
    raw_path = log_dir / "raw.bbl"

    # Each upload is isolated (no cross-user dedup) so users never share decoded
    # data or custom session names; it is reaped when the tab closes / on TTL.
    written = 0
    with open(raw_path, "wb") as out:
        while chunk := await file.read(1 << 20):
            written += len(chunk)
            if written > config.MAX_UPLOAD_BYTES:
                out.close()
                shutil.rmtree(log_dir, ignore_errors=True)
                raise HTTPException(413, "Log file too large")
            out.write(chunk)

    # Decode (subprocess + header/CSV parsing) is heavy and blocking; keep it off
    # the single event-loop thread so concurrent chart fetches / heartbeats stay
    # responsive during a large upload.
    try:
        index = await run_in_threadpool(
            session_store.register_upload, log_id, file.filename or "log.bbl"
        )
    except blackbox_decoder.DecodeError as e:
        shutil.rmtree(log_dir, ignore_errors=True)
        raise HTTPException(422, str(e))
    return index


@router.get("/api/logs/{log_id}/sessions")
def list_sessions(log_id: str):
    try:
        return session_store.get_log(log_id)
    except session_store.NotFound as e:
        raise HTTPException(404, str(e))


@router.patch("/api/logs/{log_id}/sessions/{session_id}")
def rename_session(log_id: str, session_id: int, name: str = Body("", embed=True)):
    try:
        session = session_store.rename_session(log_id, session_id, name.strip())
    except session_store.NotFound as e:
        raise HTTPException(404, str(e))
    return {"session_id": session_id, "name": session.get("name", "")}


@router.post("/api/logs/{log_id}/keepalive")
def keepalive(log_id: str):
    """Heartbeat from an open browser tab: refresh the log's last_access so the
    inactivity reaper spares it. 404 lets the client forget an already-reaped log."""
    try:
        session_store.get_log(log_id)  # raises NotFound; also refreshes last_access
    except session_store.NotFound as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.delete("/api/logs/{log_id}")
def delete_log(log_id: str):
    try:
        session_store.delete_log(log_id)
    except session_store.NotFound as e:
        raise HTTPException(404, str(e))
    return {"deleted": log_id}
