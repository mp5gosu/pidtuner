"""Compact binary transfer format for large time series.

Layout (little-endian):
    [uint32 metaLen][metaLen bytes JSON, space-padded so the payload starts
    at a multiple of 8][series payloads, each zero-padded to 8 bytes]

meta = {"series": [{"name", "dtype": "f32"|"f64", "points", "offset"}, ...],
        "extra": {...}}

`offset` is relative to the payload start (= 4 + metaLen). Because both the
header and every series payload are 8-byte aligned, the browser can create
Float32Array/Float64Array views directly on the response buffer, no copies.
"""

import json

import numpy as np

_DTYPES = {"f32": np.float32, "f64": np.float64}


def pack(series: list[tuple[str, str, np.ndarray]], extra: dict | None = None) -> bytes:
    payloads = []
    entries = []
    offset = 0
    for name, dtype, arr in series:
        raw = np.ascontiguousarray(arr, dtype=_DTYPES[dtype]).tobytes()
        raw += b"\0" * ((-len(raw)) % 8)
        entries.append({"name": name, "dtype": dtype, "points": int(len(arr)),
                        "offset": offset})
        payloads.append(raw)
        offset += len(raw)

    meta_bytes = json.dumps({"series": entries, "extra": extra or {}}).encode()
    meta_bytes += b" " * ((-(4 + len(meta_bytes))) % 8)
    return b"".join([len(meta_bytes).to_bytes(4, "little"), meta_bytes] + payloads)
