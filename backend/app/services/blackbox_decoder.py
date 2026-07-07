"""Wrapper around the official betaflight/blackbox-tools `blackbox_decode` binary.

A single .bbl file may contain several flight sessions (one per arm/disarm).
Like Plasmatree/PID-Analyzer, we split the raw file at the repeated first
header line so every session gets its own .bbl, its own header block and its
own decoded CSV.
"""

import logging
import subprocess
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)


class DecodeError(Exception):
    pass


def check_binary() -> None:
    bin_path = config.BLACKBOX_DECODE_BIN
    if not bin_path.exists():
        raise DecodeError(
            f"blackbox_decode binary not found at {bin_path}. "
            "Run scripts/build_blackbox_tools.sh or set PIDTUNER_BLACKBOX_DECODE_BIN."
        )
    result = subprocess.run(
        [str(bin_path), "--help"], capture_output=True, timeout=10
    )
    if b"Blackbox" not in result.stdout + result.stderr:
        raise DecodeError(f"{bin_path} does not look like blackbox_decode")


def split_sessions(raw_path: Path) -> list[Path]:
    """Split a multi-session .bbl into one file per session."""
    content = raw_path.read_bytes()
    newline = content.find(b"\n")
    if newline < 0:
        raise DecodeError("File contains no newline - not a blackbox log?")
    marker = content[: newline + 1]
    if not marker.startswith(b"H Product:"):
        raise DecodeError(
            "File does not start with a blackbox header ('H Product:...') - not a .bbl/.bfl log?"
        )

    parts = [p for p in content.split(marker) if p]
    session_paths = []
    for part in parts:
        data = marker + part
        if len(data) < config.MIN_SESSION_BYTES:
            log.info("Skipping tiny session (%d bytes)", len(data))
            continue
        idx = len(session_paths)
        path = raw_path.parent / f"session_{idx:02d}.bbl"
        path.write_bytes(data)
        session_paths.append(path)

    if not session_paths:
        raise DecodeError(
            "No usable flight session found in the log "
            f"(all sessions < {config.MIN_SESSION_BYTES} bytes)."
        )
    return session_paths


def decode_session(session_path: Path) -> Path:
    """Run blackbox_decode on a single-session .bbl, return the CSV path."""
    # NOTE: rotation deliberately stays in raw units. blackbox-tools does not
    # recognize the "Firmware type:Betaflight" header (new in BF 2025.12) and
    # would misapply Baseflight scaling (~5.7e7x off) with --unit-rotation
    # deg/s. We apply the gyro_scale header ourselves in csv_parser instead.
    cmd = [
        str(config.BLACKBOX_DECODE_BIN),
        "--unit-frame-time", "us",
        str(session_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=config.DECODE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as e:
        raise DecodeError(
            f"blackbox_decode timed out after {config.DECODE_TIMEOUT_S}s "
            f"for {session_path.name}"
        ) from e
    except OSError as e:
        raise DecodeError(f"Could not run blackbox_decode: {e}") from e
    csv_path = session_path.parent / (session_path.stem + ".01.csv")
    if result.returncode != 0 or not csv_path.exists():
        stderr = result.stderr.decode("utf-8", errors="replace")[-2000:]
        raise DecodeError(f"blackbox_decode failed for {session_path.name}: {stderr}")
    return csv_path


def decode_log(raw_path: Path) -> list[dict]:
    """Split + decode all sessions. Returns [{'bbl': Path, 'csv': Path}, ...]."""
    sessions = []
    for bbl in split_sessions(raw_path):
        try:
            csv = decode_session(bbl)
        except DecodeError as e:
            log.warning("Session %s failed to decode: %s", bbl.name, e)
            continue
        sessions.append({"bbl": bbl, "csv": csv})
    if not sessions:
        raise DecodeError("blackbox_decode could not decode any session in this file.")
    return sessions
