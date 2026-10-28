"""A nested ruff.toml replaces the root banned-api table instead of extending it, so a ban added at the root
would silently skip those directories. This test keeps every nested table a superset of the root one."""

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
# Bans a directory deliberately lifts, and why.
EXEMPT = {
    "northdesk/api": {"fastapi", "starlette", "temporalio"},  # the HTTP edge, holding the Temporal client
    "northdesk/workflows": {"temporalio"},  # the Temporal layer
    "northdesk/clients/llm": {"anthropic"},  # the provider SDK's one home
}


def banned(table: dict[str, object]) -> set[str]:
    lint = table.get("lint", {})
    assert isinstance(lint, dict)
    return set(lint.get("flake8-tidy-imports", {}).get("banned-api", {}))


ROOT_BANS = banned(tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["ruff"])
NESTED = sorted(path.parent.relative_to(ROOT).as_posix() for path in (ROOT / "northdesk").rglob("ruff.toml"))


def test_root_bans__cover_the_frameworks() -> None:
    assert {"fastapi", "starlette", "temporalio", "anthropic"} <= ROOT_BANS


@pytest.mark.parametrize("directory", NESTED)
def test_nested_bans__repeat_every_root_ban(directory: str) -> None:
    nested = banned(tomllib.loads((ROOT / directory / "ruff.toml").read_text()))

    assert ROOT_BANS - EXEMPT.get(directory, set()) <= nested
