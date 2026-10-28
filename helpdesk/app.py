"""Fake helpdesk: performs ticket actions once per idempotency key and records them in SQLite.

Start with `uvicorn helpdesk.app:create_app --factory`.
"""

import json
import sqlite3
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, TypedDict, cast

from fastapi import Depends, FastAPI, Header, Request
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class HelpdeskServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HELPDESK_", env_file=".env", extra="ignore")

    db_path: Path = Path("helpdesk.sqlite3")


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


class State(TypedDict):
    store: ActionStore


def get_store(request: Request) -> ActionStore:
    return cast(ActionStore, request.state.store)


StoreDep = Annotated[ActionStore, Depends(get_store)]


def create_app(settings: HelpdeskServiceSettings | None = None) -> FastAPI:
    settings = settings or HelpdeskServiceSettings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[State]:
        store = ActionStore(settings.db_path)
        try:
            yield {"store": store}
        finally:
            store.close()

    app = FastAPI(title="Fake Helpdesk", lifespan=lifespan)

    @app.post("/actions")
    def perform_action(
        body: ActionIn, store: StoreDep, idempotency_key: Annotated[str, Header(min_length=1, max_length=200)]
    ) -> ActionOut:
        return store.perform(idempotency_key, body)

    @app.get("/actions")
    def list_actions(store: StoreDep) -> list[ActionOut]:
        return store.list()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
