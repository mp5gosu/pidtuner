"""In-process registry of uploaded logs and their decoded sessions.

Storage is fully ephemeral (single worker only - documented in README):
everything under DATA_DIR is wiped on startup and on shutdown, a browser deletes
its own uploads when its tab closes, and a reaper prunes abandoned uploads.
Uploads are never deduplicated/shared between users. index.json per log dir is
just the on-disk backing for the in-process registry during runtime.
"""

import json
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

import pandas as pd

from .. import config
from . import blackbox_decoder, csv_parser

_lock = threading.Lock()
_df_cache: OrderedDict[tuple[str, int], pd.DataFrame] = OrderedDict()
_DF_CACHE_MAX = 4


class NotFound(Exception):
    pass


def _log_dir(log_id: str) -> Path:
    return config.DATA_DIR / log_id


def create_log_id() -> str:
    return uuid.uuid4().hex[:12]


def register_upload(log_id: str, filename: str) -> dict:
    """Decode an already-saved raw.bbl and persist session metadata."""
    log_dir = _log_dir(log_id)
    raw_path = log_dir / "raw.bbl"
    decoded = blackbox_decoder.decode_log(raw_path)

    sessions = []
    for idx, item in enumerate(decoded):
        headers = csv_parser.read_headers(item["bbl"])
        unfilt_available, unfilt_source = csv_parser.unfilt_availability(
            item["csv"], headers
        )
        sessions.append({
            "session_id": idx,
            "csv": item["csv"].name,
            "bbl": item["bbl"].name,
            "duration_s": round(csv_parser.quick_duration_s(item["csv"]), 2),
            "size_bytes": item["bbl"].stat().st_size,
            "sample_rate_hz": round(csv_parser.quick_sample_rate_hz(item["csv"]), 1),
            "headers": headers,
            "headers_raw": csv_parser.read_raw_headers(item["bbl"]),
            "gyro_unfilt_available": unfilt_available,
            "gyro_unfilt_source": unfilt_source,
        })

    index = {"log_id": log_id, "filename": filename,
             "uploaded_at": time.time(), "sessions": sessions}
    (log_dir / "index.json").write_text(json.dumps(index, indent=1))
    return index


def wipe_all() -> None:
    """Remove every uploaded log under DATA_DIR (but keep DATA_DIR itself, so a
    mounted volume survives). Used on startup and shutdown for ephemerality."""
    if not config.DATA_DIR.exists():
        return
    for child in config.DATA_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def cleanup_expired() -> int:
    """Delete logs older than DATA_TTL_MIN. Returns number removed."""
    if config.DATA_TTL_MIN <= 0:
        return 0
    cutoff = time.time() - config.DATA_TTL_MIN * 60
    removed = 0
    for index in list_logs():
        if index["uploaded_at"] < cutoff:
            shutil.rmtree(_log_dir(index["log_id"]), ignore_errors=True)
            removed += 1
    return removed


def list_logs() -> list[dict]:
    """All persisted logs, oldest first (upload order = test order)."""
    logs = []
    if not config.DATA_DIR.exists():
        return logs
    for index_path in config.DATA_DIR.glob("*/index.json"):
        try:
            index = json.loads(index_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        index.setdefault("uploaded_at", index_path.stat().st_mtime)
        logs.append(index)
    logs.sort(key=lambda x: x["uploaded_at"])
    return logs


def get_log(log_id: str) -> dict:
    index_path = _log_dir(log_id) / "index.json"
    if not index_path.exists():
        raise NotFound(f"Unknown log_id {log_id}")
    return json.loads(index_path.read_text())


def get_session(log_id: str, session_id: int) -> dict:
    log = get_log(log_id)
    for s in log["sessions"]:
        if s["session_id"] == session_id:
            return s
    raise NotFound(f"Unknown session {session_id} for log {log_id}")


def get_dataframe(log_id: str, session_id: int) -> pd.DataFrame:
    key = (log_id, session_id)
    with _lock:
        if key in _df_cache:
            _df_cache.move_to_end(key)
            return _df_cache[key]
    session = get_session(log_id, session_id)
    df = csv_parser.load_dataframe(_log_dir(log_id) / session["csv"])
    with _lock:
        _df_cache[key] = df
        while len(_df_cache) > _DF_CACHE_MAX:
            _df_cache.popitem(last=False)
    return df


def rename_session(log_id: str, session_id: int, name: str) -> dict:
    """Set (or clear, if name is blank) a user-facing name for one session.
    Written into index.json so it holds for the lifetime of this upload (storage
    is ephemeral, so it does not survive a restart or a fresh re-upload)."""
    index_path = _log_dir(log_id) / "index.json"
    if not index_path.exists():
        raise NotFound(f"Unknown log_id {log_id}")
    with _lock:
        index = json.loads(index_path.read_text())
        session = next(
            (s for s in index["sessions"] if s["session_id"] == session_id), None)
        if session is None:
            raise NotFound(f"Unknown session {session_id} for log {log_id}")
        if name:
            session["name"] = name
        else:
            session.pop("name", None)
        index_path.write_text(json.dumps(index, indent=1))
    return session


def delete_log(log_id: str) -> None:
    log_dir = _log_dir(log_id)
    if not log_dir.exists():
        raise NotFound(f"Unknown log_id {log_id}")
    with _lock:
        for key in [k for k in _df_cache if k[0] == log_id]:
            del _df_cache[key]
    shutil.rmtree(log_dir)
