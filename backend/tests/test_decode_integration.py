"""End-to-end pipeline check: synthesize a real blackbox stream, split + decode
it with the actual blackbox_decode binary, and load the CSV into a DataFrame.

Skipped when the binary hasn't been built (see scripts/build_blackbox_tools.sh);
CI builds it first so this runs there.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from app import config
from app.services import blackbox_decoder, csv_parser

BACKEND = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    not config.BLACKBOX_DECODE_BIN.exists(),
    reason="blackbox_decode binary not built",
)


def _make_bbl(dest: Path, sessions: int = 1) -> None:
    subprocess.run(
        [sys.executable, "tests/make_synthetic_bbl.py", str(dest),
         "--sessions", str(sessions)],
        cwd=BACKEND, check=True, capture_output=True,
    )


def test_decode_and_load_dataframe(tmp_path):
    raw = tmp_path / "raw.bbl"
    _make_bbl(raw, sessions=1)

    decoded = blackbox_decoder.decode_log(raw)
    assert len(decoded) == 1

    df = csv_parser.load_dataframe(decoded[0]["csv"])
    assert "time" in df.columns
    assert len(df) > 1000

    # per csv_parser.load_dataframe: time stays float64, the bulk is float32.
    assert str(df["time"].dtype) == "float64"
    gyro_cols = [c for c in df.columns if c.startswith("gyroADC[")]
    assert gyro_cols
    assert str(df[gyro_cols[0]].dtype) == "float32"


def test_split_sessions_counts_multiple(tmp_path):
    raw = tmp_path / "raw.bbl"
    _make_bbl(raw, sessions=2)
    parts = blackbox_decoder.split_sessions(raw)
    assert len(parts) == 2
