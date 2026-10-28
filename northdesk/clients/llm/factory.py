"""Picks the LLM client for LLM_MODE. The only module outside clients/llm/ that knows the providers exist."""

from northdesk.clients.llm.anthropic_client import AnthropicLlm
from northdesk.clients.llm.base import LlmClient
from northdesk.clients.llm.fake import FakeLlm
from northdesk.settings import LlmSettings


def build_llm(settings: LlmSettings) -> LlmClient:
    if settings.mode == "fake":
        return FakeLlm()
    assert settings.api_key is not None  # LlmSettings refuses real mode without a key  # noqa: S101
    return AnthropicLlm(
        api_key=settings.api_key.get_secret_value(), model=settings.model, timeout_seconds=settings.timeout_seconds
    )
