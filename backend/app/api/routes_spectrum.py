from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from ..services import session_store, spectrum

router = APIRouter()


def _serialize(result: dict) -> dict:
    out = {
        "fs": round(float(result["fs"]), 2),
        "nyquist": round(float(result["nyquist"]), 2),
        "unfilt_source": result["unfilt_source"],
        "freq": [round(float(v), 2) for v in result["freq"]],
        "axes": {},
    }
    for axis, entry in result["axes"].items():
        out["axes"][axis] = {
            key: ([round(float(v), 4) for v in entry[key]] if entry[key] is not None else None)
            for key in ("filtered", "unfiltered")
        }
    return out


def _compute(log_id: str, session_id: int) -> dict:
    session = session_store.get_session(log_id, session_id)
    df = session_store.get_dataframe(log_id, session_id)
    return _serialize(spectrum.compute(df, session))


@router.get("/api/logs/{log_id}/sessions/{session_id}/spectrum")
async def get_spectrum(log_id: str, session_id: int):
    try:
        return await run_in_threadpool(_compute, log_id, session_id)
    except session_store.NotFound as e:
        raise HTTPException(404, str(e))
    except spectrum.SpectrumError as e:
        raise HTTPException(422, str(e))
