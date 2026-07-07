"""Unit tests for the registry's safety-critical bits: log_id validation
(path-traversal guard), atomic index read/write, and DataFrame-cache eviction."""

import pandas as pd
import pytest

from app.services import session_store


@pytest.mark.parametrize("bad", [
    "..", ".", "a/b", "", "../../etc", "ABCDEF012345",   # uppercase not allowed
    "0123456789ab0", "short", "xyz", "0123456789a",       # wrong length / non-hex
])
def test_log_dir_rejects_bad_log_id(bad):
    with pytest.raises(session_store.NotFound):
        session_store._log_dir(bad)


def test_log_dir_accepts_generated_id():
    lid = session_store.create_log_id()
    assert session_store._log_dir(lid).name == lid


def test_atomic_index_roundtrip(tmp_path):
    p = tmp_path / "index.json"
    data = {"log_id": "abcdef012345", "sessions": [1, 2, 3], "last_access": 1.5}
    session_store._write_index(p, data)
    assert session_store._read_index(p) == data
    assert not (tmp_path / "index.json.tmp").exists()  # temp cleaned up


def test_read_index_corrupt_maps_to_notfound(tmp_path):
    p = tmp_path / "index.json"
    p.write_text("{ this is not json")
    with pytest.raises(session_store.NotFound):
        session_store._read_index(p)


def test_read_index_missing_maps_to_notfound(tmp_path):
    with pytest.raises(session_store.NotFound):
        session_store._read_index(tmp_path / "nope.json")


def test_evict_df_cache_drops_only_matching_log():
    keep = ("aaaaaaaaaaaa", 1)
    drop = ("bbbbbbbbbbbb", 0)
    session_store._df_cache[keep] = pd.DataFrame({"y": [1]})
    session_store._df_cache[drop] = pd.DataFrame({"x": [1]})
    try:
        session_store._evict_df_cache("bbbbbbbbbbbb")
        assert drop not in session_store._df_cache
        assert keep in session_store._df_cache
    finally:
        session_store._df_cache.clear()
