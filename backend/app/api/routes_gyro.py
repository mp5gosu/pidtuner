from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool

from .. import config
from ..core import binary_pack
from ..services import gyro_series, session_store

router = APIRouter()


def _build_payload(log_id: str, session_id: int, max_points: int) -> bytes:
    session = session_store.get_session(log_id, session_id)
    df = session_store.get_dataframe(log_id, session_id)
    data = gyro_series.extract(df, session, max_points)

    series = []
    axes_meta = {}
    for axis, entry in data["axes"].items():
        signals = []
        for key, arr in entry.items():
            dtype = "f64" if key == "time_s" else "f32"
            series.append((f"{axis}.{key}", dtype, arr))
            if key != "time_s":
                signals.append(key)
        axes_meta[axis] = {"signals": signals}

    return binary_pack.pack(series, extra={
        "axes": axes_meta,
        "unfilt_source": data["unfilt_source"],
    })


@router.get("/api/logs/{log_id}/sessions/{session_id}/gyro")
async def get_gyro(log_id: str, session_id: int,
                   max_points: int = Query(default=config.MAX_CHART_POINTS, le=2_000_000)):
    try:
        payload = await run_in_threadpool(_build_payload, log_id, session_id, max_points)
    except session_store.NotFound as e:
        raise HTTPException(404, str(e))
    return Response(content=payload, media_type="application/octet-stream")
