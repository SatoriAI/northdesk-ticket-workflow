"""The real LLM: Claude through the Anthropic SDK, behind the same LlmClient interface as the fake.

It does not retry: SDK retries are off so Temporal's decide retry policy is the only retry layer. Failures are
classified like the helpdesk client's: temporary ones (rate limits, overload, 5xx, timeouts, connection errors)
are retried; a permanent one (bad key, bad request) or a refusal is not, and the ticket escalates.
"""

from typing import cast

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam, BetaToolParam

from northdesk.clients.llm.base import LlmTurn, Message, ToolCall, ToolSpec

# Opt in to server-side fallback: if a safety classifier declines, the API re-runs the request on a fallback model
# within the same call, instead of the ticket failing on a false positive.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_MAX_TOKENS = 16_000
# Triage is a short task; set explicitly because the default differs between model generations.
_OUTPUT_CONFIG: BetaOutputConfigParam = {"effort": "medium"}


class LlmUnavailableError(Exception):
    """Temporary: the same request may succeed on a retry."""


class LlmRejectedError(Exception):
    """Permanent: retrying the same request cannot succeed (bad credentials, bad request, refusal)."""


class AnthropicLlm:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        http_client: anthropic.DefaultAsyncHttpxClient | None = None,
    ) -> None:
        self._model = model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, max_retries=0, timeout=timeout_seconds, http_client=http_client
        )

    async def complete(self, *, system: str, messages: list[Message], tools: list[ToolSpec]) -> LlmTurn:
        try:
            response = await self._client.beta.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=system,
                # The loop builds plain dicts in the API's own shape (and echoes the SDK's blocks back as they came).
                messages=cast(list[BetaMessageParam], messages),
                tools=cast(list[BetaToolParam], tools),
                output_config=_OUTPUT_CONFIG,
                betas=[_FALLBACK_BETA],
                fallbacks="default",
            )
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            raise LlmUnavailableError(f"LLM unavailable: {type(exc).__name__}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code in {408, 409, 429} or exc.status_code >= 500:
                raise LlmUnavailableError(f"LLM unavailable: HTTP {exc.status_code}") from exc
            raise LlmRejectedError(f"LLM rejected the request: HTTP {exc.status_code}") from exc

        if response.stop_reason == "refusal":
            raise LlmRejectedError("the model declined to answer")
        return LlmTurn(
            text="".join(block.text for block in response.content if block.type == "text"),
            tool_calls=[
                ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                for block in response.content
                if block.type == "tool_use"
            ],
            # Echoed back unchanged on the next turn, thinking blocks included, as the API requires; `fallback` blocks
            # are audit markers only and are left out.
            raw_content=[block for block in response.content if block.type != "fallback"],
        )
