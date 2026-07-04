from fastapi import APIRouter, HTTPException, Response
from fastapi.concurrency import run_in_threadpool

from ..core import binary_pack
from ..services import noise_throttle, session_store

router = APIRouter()

# 2D throttle x frequency maps are large; ship them as flat float32 payloads
# (reshaped on the client with n_freq/n_throttle) rather than JSON. Per-map
# stats and the shared colour scale ride along in the meta header.


def _build_payload(log_id: str, session_id: int) -> bytes:
    session = session_store.get_session(log_id, session_id)
    df = session_store.get_dataframe(log_id, session_id)
    result = noise_throttle.compute(df, session)

    freq = result["freq"]
    series = [
        ("freq", "f32", freq),
        ("throttle", "f32", result["throttle"]),
    ]
    axes_meta = {}
    for axis, entry in result["axes"].items():
        signals = {}
        for key, cell in entry.items():
            if cell is None:
                continue
            series.append((f"{axis}.{key}", "f32", cell["grid"].ravel()))
            signals[key] = {"mean": round(cell["mean"], 4), "peak": round(cell["peak"], 4)}
        axes_meta[axis] = {"signals": signals}

    return binary_pack.pack(series, extra={
        "n_freq": int(len(freq)),
        "n_throttle": int(len(result["throttle"])),
        "fs": round(float(result["fs"]), 2),
        "nyquist": round(float(result["nyquist"]), 2),
        "vmax": round(float(result["vmax"]), 6),
        "unfilt_source": result["unfilt_source"],
        "axes": axes_meta,
    })


@router.get("/api/logs/{log_id}/sessions/{session_id}/noise-throttle")
async def get_noise_throttle(log_id: str, session_id: int):
    try:
        payload = await run_in_threadpool(_build_payload, log_id, session_id)
    except session_store.NotFound as e:
        raise HTTPException(404, str(e))
    except noise_throttle.NoiseThrottleError as e:
        raise HTTPException(422, str(e))
    return Response(content=payload, media_type="application/octet-stream")
