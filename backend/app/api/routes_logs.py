import hashlib
import logging
import shutil

from fastapi import APIRouter, Body, HTTPException, Request, UploadFile

from .. import config
from ..services import blackbox_decoder, session_store

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/logs")
async def upload_log(request: Request, file: UploadFile):
    length = request.headers.get("content-length")
    if length and int(length) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Log file too large")

    log_id = session_store.create_log_id()
    log_dir = config.DATA_DIR / log_id
    log_dir.mkdir(parents=True, exist_ok=True)
    raw_path = log_dir / "raw.bbl"

    written = 0
    sha = hashlib.sha256()
    with open(raw_path, "wb") as out:
        while chunk := await file.read(1 << 20):
            written += len(chunk)
            if written > config.MAX_UPLOAD_BYTES:
                out.close()
                shutil.rmtree(log_dir, ignore_errors=True)
                raise HTTPException(413, "Log file too large")
            sha.update(chunk)
            out.write(chunk)

    # identical content already on disk? reuse instead of duplicating
    existing = session_store.find_by_sha256(sha.hexdigest())
    if existing:
        shutil.rmtree(log_dir, ignore_errors=True)
        log.info("Upload is a duplicate of log %s, reusing", existing["log_id"])
        return {**existing, "duplicate_of_existing": True}

    try:
        index = session_store.register_upload(
            log_id, file.filename or "log.bbl", sha256=sha.hexdigest())
    except blackbox_decoder.DecodeError as e:
        shutil.rmtree(log_dir, ignore_errors=True)
        raise HTTPException(422, str(e))
    return index


@router.get("/api/logs")
def list_logs():
    return {"logs": session_store.list_logs()}


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


@router.delete("/api/logs/{log_id}")
def delete_log(log_id: str):
    try:
        session_store.delete_log(log_id)
    except session_store.NotFound as e:
        raise HTTPException(404, str(e))
    return {"deleted": log_id}
