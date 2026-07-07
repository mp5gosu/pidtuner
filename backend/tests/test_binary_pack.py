"""Round-trip the custom typed-array transfer format (core/binary_pack.py).

This is the contract the browser's zero-copy Float32Array/Float64Array views in
js/api.js rely on, so it is worth pinning: 8-byte alignment of the header and
every payload, correct point counts (incl. raveled 2D grids), and dtype fidelity.
"""

import json

import numpy as np

from app.core import binary_pack


def _unpack(blob: bytes):
    """Mirror of js/api.js fetchBinarySeries, in numpy."""
    meta_len = int.from_bytes(blob[:4], "little")
    meta = json.loads(blob[4:4 + meta_len].decode())
    payload_start = 4 + meta_len
    out = {}
    for s in meta["series"]:
        dt = np.float32 if s["dtype"] == "f32" else np.float64
        out[s["name"]] = np.frombuffer(
            blob, dtype=dt, count=s["points"], offset=payload_start + s["offset"]
        )
    return meta, out


def test_roundtrip_values_and_extra():
    t = np.linspace(0, 1, 1000, dtype=np.float64)
    g = (np.sin(t) * 100).astype(np.float32)
    blob = binary_pack.pack([("time", "f64", t), ("gyro", "f32", g)],
                            extra={"axes": ["roll"], "fs": 2000.0})
    meta, out = _unpack(blob)

    assert meta["extra"] == {"axes": ["roll"], "fs": 2000.0}
    np.testing.assert_allclose(out["time"], t)
    np.testing.assert_allclose(out["gyro"], g)


def test_header_and_payloads_are_8byte_aligned():
    # 3 f32 = 12 bytes must pad to 16 so the next payload stays 8-aligned.
    a = np.arange(3, dtype=np.float32)
    b = np.arange(5, dtype=np.float64)
    blob = binary_pack.pack([("a", "f32", a), ("b", "f64", b)])

    meta_len = int.from_bytes(blob[:4], "little")
    assert (4 + meta_len) % 8 == 0
    meta = json.loads(blob[4:4 + meta_len].decode())
    for s in meta["series"]:
        assert s["offset"] % 8 == 0


def test_grid_ravel_points_and_shape():
    # 2D grids ride as flat payloads; points is the raveled length, reshaped
    # client-side from extra dims.
    grid = np.arange(12, dtype=np.float32).reshape(3, 4)
    blob = binary_pack.pack([("m", "f32", grid.ravel())],
                            extra={"n_rows": 3, "n_cols": 4})
    meta, out = _unpack(blob)

    assert meta["series"][0]["points"] == 12
    np.testing.assert_allclose(out["m"].reshape(3, 4), grid)


def test_empty_series_list():
    blob = binary_pack.pack([], extra={"note": "no series"})
    meta, out = _unpack(blob)
    assert meta["series"] == []
    assert out == {}
    assert meta["extra"] == {"note": "no series"}
