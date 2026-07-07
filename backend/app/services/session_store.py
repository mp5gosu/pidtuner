"""In-process registry of uploaded logs and their decoded sessions.

Storage is fully ephemeral (single worker only - documented in README):
everything under DATA_DIR is wiped on startup and on shutdown, and a reaper
prunes logs that have gone untouched for DATA_TTL_MIN. Every access refreshes a
log's ``last_access`` (see ``touch``/``get_log``) and open browser tabs send a
keepalive heartbeat, so expiry is inactivity-based: an accidental page reload
re-attaches within seconds and does not lose data. Uploads are never
deduplicated/shared between users. index.json per log dir is just the on-disk
backing for the in-process registry during runtime.
"""

import json
import os
import re
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

# log_ids are produced by create_log_id() as uuid4().hex[:12]. Enforcing that
# shape rejects path-traversal ids ('..', '.', 'a/b') before they ever reach a
# filesystem call — see delete_log()/get_log(), which would otherwise rmtree /
# read arbitrary directories relative to DATA_DIR.
_LOG_ID_RE = re.compile(r"^[0-9a-f]{12}$")


class NotFound(Exception):
    pass


def _log_dir(log_id: str) -> Path:
    if not isinstance(log_id, str) or not _LOG_ID_RE.match(log_id):
        raise NotFound(f"Unknown log_id {log_id!r}")
    return config.DATA_DIR / log_id


def create_log_id() -> str:
    return uuid.uuid4().hex[:12]


def _write_index(index_path: Path, index: dict) -> None:
    """Atomically persist an index.json. Writers use truncate-then-write via a
    temp sibling + os.replace so a concurrent reader never observes a partial
    file (which would raise JSONDecodeError on the request hot path)."""
    tmp = index_path.with_name(index_path.name + ".tmp")
    tmp.write_text(json.dumps(index, indent=1))
    os.replace(tmp, index_path)


def _read_index(index_path: Path) -> dict:
    """Read+parse an index.json, mapping a missing/corrupt file to NotFound so
    callers surface a clean 404 instead of a 500 (e.g. reaped mid-request)."""
    try:
        return json.loads(index_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise NotFound(f"Log index unavailable at {index_path}") from e


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

    now = time.time()
    index = {"log_id": log_id, "filename": filename,
             "uploaded_at": now, "last_access": now, "sessions": sessions}
    _write_index(log_dir / "index.json", index)
    return index


def touch(log_id: str) -> None:
    """Refresh a log's last_access so the inactivity reaper keeps it alive.
    Best-effort: a missing/half-written index just isn't refreshed this time."""
    index_path = _log_dir(log_id) / "index.json"
    try:
        with _lock:
            index = json.loads(index_path.read_text())
            index["last_access"] = time.time()
            _write_index(index_path, index)
    except (OSError, json.JSONDecodeError):
        pass


def _evict_df_cache(log_id: str) -> None:
    """Drop any cached DataFrames for a log. Caller must hold _lock. Without this
    the LRU pins frames of reaped/wiped logs in memory until eviction."""
    for key in [k for k in _df_cache if k[0] == log_id]:
        del _df_cache[key]


def wipe_all() -> None:
    """Remove every uploaded log under DATA_DIR (but keep DATA_DIR itself, so a
    mounted volume survives). Used on startup and shutdown for ephemerality."""
    with _lock:
        _df_cache.clear()
    if not config.DATA_DIR.exists():
        return
    for child in config.DATA_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def cleanup_expired() -> int:
    """Delete logs untouched for DATA_TTL_MIN (inactivity). Returns number removed."""
    if config.DATA_TTL_MIN <= 0:
        return 0
    cutoff = time.time() - config.DATA_TTL_MIN * 60
    removed = 0
    for index in list_logs():
        if index.get("last_access", index["uploaded_at"]) < cutoff:
            log_id = index["log_id"]
            with _lock:
                _evict_df_cache(log_id)
                shutil.rmtree(_log_dir(log_id), ignore_errors=True)
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
    # Any access is activity: refresh last_access so the reaper spares logs in
    # use. This is the choke point for reads (get_session and list_sessions both
    # route through here); the reaper reads via list_logs and never self-refreshes.
    touch(log_id)
    return _read_index(index_path)


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
        index = _read_index(index_path)
        session = next(
            (s for s in index["sessions"] if s["session_id"] == session_id), None)
        if session is None:
            raise NotFound(f"Unknown session {session_id} for log {log_id}")
        if name:
            session["name"] = name
        else:
            session.pop("name", None)
        _write_index(index_path, index)
    return session


def delete_log(log_id: str) -> None:
    log_dir = _log_dir(log_id)
    if not log_dir.exists():
        raise NotFound(f"Unknown log_id {log_id}")
    with _lock:
        _evict_df_cache(log_id)
        shutil.rmtree(log_dir)
