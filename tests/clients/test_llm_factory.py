from pydantic import SecretStr

from northdesk.clients.llm.anthropic_client import AnthropicLlm
from northdesk.clients.llm.factory import build_llm
from northdesk.clients.llm.fake import FakeLlm
from northdesk.settings import LlmSettings


def test_build_llm__fake_mode_needs_no_key() -> None:
    assert isinstance(build_llm(LlmSettings(mode="fake", api_key=None)), FakeLlm)


def test_build_llm__real_mode_builds_the_configured_provider() -> None:
    settings = LlmSettings(mode="real", provider="anthropic", api_key=SecretStr("test-key"))

    assert isinstance(build_llm(settings), AnthropicLlm)
