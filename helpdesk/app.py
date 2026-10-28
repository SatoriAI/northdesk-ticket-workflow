"""Fake helpdesk: performs ticket actions once per idempotency key and records them in SQLite.

It misbehaves on purpose, at seeded random rates, so the worker's retries are exercised:
- a temporary failure (503) before anything is performed;
- a lost response: the action is committed, then a 503 is returned as if the reply never arrived.
A retry with the same Idempotency-Key returns the original result with `replayed: true`.

Start with `uvicorn helpdesk.app:create_app --factory`.
"""

import json
import logging
import random
import sqlite3
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, TypedDict, cast

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HelpdeskServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HELPDESK_", env_file=".env", extra="ignore")

    db_path: Path = Path("helpdesk.sqlite3")
    failure_rate: float = Field(default=0.0, ge=0, le=1)
    lost_response_rate: float = Field(default=0.0, ge=0, le=1)
    seed: int | None = None


logger = logging.getLogger(__name__)


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    action: str
    details: dict[str, Any]


class ActionOut(BaseModel):
    action_id: str
    idempotency_key: str
    ticket_id: str
    action: str
    details: dict[str, Any]
    replayed: bool


class ActionStore:
    """Sync SQLite behind a lock; the endpoints are sync `def`, so FastAPI runs them in its threadpool."""

    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS actions ("
            " idempotency_key TEXT PRIMARY KEY, action_id TEXT NOT NULL, ticket_id TEXT NOT NULL,"
            " action TEXT NOT NULL, details TEXT NOT NULL)"
        )

    def perform(self, key: str, request: ActionIn) -> ActionOut:
        with self._lock, self._db:
            inserted = self._db.execute(
                "INSERT OR IGNORE INTO actions VALUES (?, ?, ?, ?, ?)",
                (key, uuid.uuid4().hex, request.ticket_id, request.action, json.dumps(request.details)),
            ).rowcount
            row = self._db.execute("SELECT * FROM actions WHERE idempotency_key = ?", (key,)).fetchone()
        return self._to_out(row, replayed=inserted == 0)

    def list(self) -> list[ActionOut]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM actions ORDER BY rowid").fetchall()
        return [self._to_out(row, replayed=False) for row in rows]

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _to_out(row: sqlite3.Row, *, replayed: bool) -> ActionOut:
        return ActionOut(
            action_id=row["action_id"],
            idempotency_key=row["idempotency_key"],
            ticket_id=row["ticket_id"],
            action=row["action"],
            details=json.loads(row["details"]),
            replayed=replayed,
        )


class Faults:
    """Decides, from one seeded RNG, which requests fail. Seeded so a run can be reproduced."""

    def __init__(self, settings: HelpdeskServiceSettings, rng: random.Random) -> None:
        self._failure_rate = settings.failure_rate
        self._lost_response_rate = settings.lost_response_rate
        self._rng = rng
        self._lock = threading.Lock()

    def fail_before_performing(self) -> bool:
        return self._roll(self._failure_rate)

    def lose_response(self) -> bool:
        return self._roll(self._lost_response_rate)

    def _roll(self, rate: float) -> bool:
        with self._lock:
            return self._rng.random() < rate


class State(TypedDict):
    store: ActionStore
    faults: Faults


def get_store(request: Request) -> ActionStore:
    return cast(ActionStore, request.state.store)


def get_faults(request: Request) -> Faults:
    return cast(Faults, request.state.faults)


StoreDep = Annotated[ActionStore, Depends(get_store)]
FaultsDep = Annotated[Faults, Depends(get_faults)]

_TEMPORARY_FAILURE: dict[int | str, dict[str, Any]] = {
    503: {"description": "Temporary failure, or the action was performed and the response lost"}
}


def create_app(settings: HelpdeskServiceSettings | None = None, rng: random.Random | None = None) -> FastAPI:
    """`rng` lets tests script exactly which request fails; otherwise it is seeded from HELPDESK_SEED."""
    settings = settings or HelpdeskServiceSettings()
    rng = rng or random.Random(settings.seed)  # noqa: S311 - fault injection, not security

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[State]:
        store = ActionStore(settings.db_path)
        try:
            yield {"store": store, "faults": Faults(settings, rng)}
        finally:
            store.close()

    app = FastAPI(title="Fake Helpdesk", lifespan=lifespan)

    @app.post("/actions", response_model=ActionOut, responses=_TEMPORARY_FAILURE)
    def perform_action(
        body: ActionIn,
        store: StoreDep,
        faults: FaultsDep,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
    ) -> ActionOut | JSONResponse:
        if faults.fail_before_performing():
            logger.info("temporary failure for %s, nothing performed", idempotency_key)
            return JSONResponse({"detail": "temporary failure"}, status_code=503)
        result = store.perform(idempotency_key, body)  # committed from here on
        if faults.lose_response():
            logger.info("performed %s, then lost the response", idempotency_key)
            return JSONResponse({"detail": "response lost"}, status_code=503)
        return result

    @app.get("/actions")
    def list_actions(store: StoreDep) -> list[ActionOut]:
        return store.list()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
