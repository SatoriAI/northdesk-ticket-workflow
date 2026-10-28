from pathlib import Path

import pytest
from pydantic import ValidationError

from helpdesk.app import HelpdeskServiceSettings
from northdesk.settings import ApiSettings, LlmSettings, WorkerSettings


def test_llm_settings__fake_mode_needs_no_key() -> None:
    settings = LlmSettings(mode="fake", api_key=None)

    assert settings.api_key is None


def test_llm_settings__real_mode_without_key_fails() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY is required"):
        LlmSettings(mode="real", api_key=None)


def test_llm_settings__real_mode_with_empty_key_fails() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY is required"):
        LlmSettings(mode="real", api_key="")


def test_settings__copied_env_example_uses_the_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every name in .env.example is empty; set them all empty and the defaults must still apply."""
    env_example = Path(__file__).parents[2] / ".env.example"
    monkeypatch.chdir(tmp_path)  # away from the repo, so a developer's own .env cannot leak in
    for line in env_example.read_text().splitlines():
        if line and not line.startswith("#"):
            monkeypatch.setenv(line.split("=", 1)[0], "")

    api, worker, helpdesk = ApiSettings(), WorkerSettings(), HelpdeskServiceSettings()

    assert (api.enable_docs, worker.llm.mode, worker.llm.api_key) == (False, "fake", None)
    assert (worker.helpdesk.timeout_seconds, helpdesk.failure_rate, helpdesk.seed) == (5.0, 0.0, None)
