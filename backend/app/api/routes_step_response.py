import json

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from .. import config
from ..services import session_store, step_response

router = APIRouter()

# bump when the algorithm changes so stale disk caches are ignored
_CACHE_VERSION = 1


def _compute(log_id: str, session_id: int) -> dict:
    session = session_store.get_session(log_id, session_id)

    cache_path = config.DATA_DIR / log_id / f"stepresp_v{_CACHE_VERSION}_{session_id}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())

    df = session_store.get_dataframe(log_id, session_id)
    result = step_response.compute(df, session)

    # numpy arrays -> lists for JSON; responses are small (~2-4k points/axis)
    for axis_data in result["axes"].values():
        for key in ("time_ms", "consensus", "low", "high"):
            if key in axis_data and axis_data[key] is not None:
                axis_data[key] = [round(float(v), 5) for v in axis_data[key]]

    cache_path.write_text(json.dumps(result))
    return result


@router.get("/api/logs/{log_id}/sessions/{session_id}/step-response")
async def get_step_response(log_id: str, session_id: int):
    try:
        return await run_in_threadpool(_compute, log_id, session_id)
    except session_store.NotFound as e:
        raise HTTPException(404, str(e))
    except step_response.StepResponseError as e:
        raise HTTPException(422, str(e))
