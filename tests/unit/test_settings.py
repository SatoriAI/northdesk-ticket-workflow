import pytest
from pydantic import ValidationError

from northdesk.settings import LlmSettings


def test_llm_settings__fake_mode_needs_no_key() -> None:
    settings = LlmSettings(mode="fake", api_key=None)

    assert settings.api_key is None


def test_llm_settings__real_mode_without_key_fails() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY is required"):
        LlmSettings(mode="real", api_key=None)


def test_llm_settings__real_mode_with_empty_key_fails() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY is required"):
        LlmSettings(mode="real", api_key="")
