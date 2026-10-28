from datetime import timedelta

from fastapi import APIRouter

from northdesk.api.deps import TemporalClientDep
from northdesk.api.handlers import is_temporal_unavailable
from northdesk.api.problems import problem_responses
from northdesk.errors import DependencyUnavailableError

router = APIRouter(prefix="/health", tags=["health"])

# Readiness must answer well inside a probe timeout.
READINESS_TIMEOUT = timedelta(seconds=2)


@router.get("/live", summary="Liveness: the process answers")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready", summary="Readiness: Temporal is reachable (a worker is not checked)", responses=problem_responses(503)
)
async def ready(client: TemporalClientDep) -> dict[str, str]:
    try:
        serving = await client.service_client.check_health(timeout=READINESS_TIMEOUT)
    except Exception as exc:
        if not is_temporal_unavailable(exc):
            raise
        raise DependencyUnavailableError("Temporal is not reachable") from exc
    if not serving:
        raise DependencyUnavailableError("Temporal is not serving")
    return {"status": "ok"}
