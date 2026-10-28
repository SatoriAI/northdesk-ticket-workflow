from datetime import timedelta

from fastapi import APIRouter
from temporalio.service import RPCError

from northdesk.api.deps import TemporalClientDep
from northdesk.api.problems import problem_responses
from northdesk.constants import READINESS_TEMPORAL_TIMEOUT_SECONDS
from northdesk.errors import DependencyUnavailableError

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", responses=problem_responses(503))
async def ready(client: TemporalClientDep) -> dict[str, str]:
    try:
        serving = await client.service_client.check_health(
            timeout=timedelta(seconds=READINESS_TEMPORAL_TIMEOUT_SECONDS)
        )
    except (RPCError, RuntimeError) as exc:  # RuntimeError: a lazy client that cannot connect
        raise DependencyUnavailableError("Temporal is not reachable") from exc
    if not serving:
        raise DependencyUnavailableError("Temporal is not serving")
    return {"status": "ok"}
