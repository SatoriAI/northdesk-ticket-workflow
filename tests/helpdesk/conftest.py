import random
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from helpdesk.app import HelpdeskServiceSettings, create_app


class ScriptedRandom(random.Random):
    """Returns the given values from random(), in order, so a test decides exactly which request fails.

    The helpdesk rolls twice per request: first "fail before performing", then "lose the response".
    A roll below the rate triggers the fault, so 0.0 means "fault" and 0.99 means "no fault".
    """

    def __init__(self, rolls: list[float]) -> None:
        super().__init__()
        self.rolls = rolls

    def random(self) -> float:
        return self.rolls.pop(0)


OK = [0.99, 0.99]
FAIL_BEFORE = [0.0]
LOSE_RESPONSE = [0.99, 0.0]


@pytest.fixture
def rolls() -> list[float]:
    return []


@pytest.fixture
async def helpdesk(tmp_path: Path, rolls: list[float]) -> AsyncIterator[httpx.AsyncClient]:
    settings = HelpdeskServiceSettings(db_path=tmp_path / "helpdesk.sqlite3", failure_rate=0.5, lost_response_rate=0.5)
    app = create_app(settings, rng=ScriptedRandom(rolls))
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://helpdesk") as client,
    ):
        yield client
