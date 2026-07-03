"""In-process registry of uploaded logs and their decoded sessions.

Metadata is persisted as index.json per log dir, so uploads survive a backend
restart (single worker only - documented in README).
"""

import hashlib
import json
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


def register_upload(log_id: str, filename: str, sha256: str | None = None) -> dict:
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
            "headers": headers,
            "headers_raw": csv_parser.read_raw_headers(item["bbl"]),
            "gyro_unfilt_available": unfilt_available,
            "gyro_unfilt_source": unfilt_source,
        })

    index = {"log_id": log_id, "filename": filename, "sha256": sha256,
             "uploaded_at": time.time(), "sessions": sessions}
    (log_dir / "index.json").write_text(json.dumps(index, indent=1))
    return index


def _backfill_headers_raw(index: dict) -> bool:
    """Populate 'headers_raw' for sessions of a legacy index.json that predates
    the field, re-parsing from the retained per-session .bbl. Persists and
    returns True if anything changed. Mirrors the sha256 backfill below."""
    log_dir = _log_dir(index["log_id"])
    changed = False
    for s in index.get("sessions", []):
        if s.get("headers_raw"):
            continue
        bbl = log_dir / s.get("bbl", "")
        if not bbl.exists():
            continue
        s["headers_raw"] = csv_parser.read_raw_headers(bbl)
        changed = True
    if changed:
        (log_dir / "index.json").write_text(json.dumps(index, indent=1))
    return changed


def cleanup_expired() -> int:
    """Delete logs older than DATA_TTL_DAYS. Returns number removed."""
    import shutil

    if config.DATA_TTL_DAYS <= 0:
        return 0
    cutoff = time.time() - config.DATA_TTL_DAYS * 86400
    removed = 0
    for index in list_logs():
        if index["uploaded_at"] < cutoff:
            shutil.rmtree(_log_dir(index["log_id"]), ignore_errors=True)
            removed += 1
    return removed


def find_by_sha256(digest: str) -> dict | None:
    """Existing log with identical raw content, or None. Backfills the hash
    into legacy index.json files that predate this field."""
    for index in list_logs():
        sha = index.get("sha256")
        if sha is None:
            raw = _log_dir(index["log_id"]) / "raw.bbl"
            if not raw.exists():
                continue
            with open(raw, "rb") as f:
                sha = hashlib.file_digest(f, "sha256").hexdigest()
            index["sha256"] = sha
            (_log_dir(index["log_id"]) / "index.json").write_text(
                json.dumps(index, indent=1))
        if sha == digest:
            _backfill_headers_raw(index)
            return index
    return None


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
    index = json.loads(index_path.read_text())
    _backfill_headers_raw(index)
    return index


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
    Persisted into index.json so it survives a restart and re-appears when the
    same file is re-uploaded (dedup returns the existing index)."""
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
    import shutil

    log_dir = _log_dir(log_id)
    if not log_dir.exists():
        raise NotFound(f"Unknown log_id {log_id}")
    with _lock:
        for key in [k for k in _df_cache if k[0] == log_id]:
            del _df_cache[key]
    shutil.rmtree(log_dir)
