"""The provider-neutral LLM interface the agent loop talks to. The fake and the real client both implement it.

Messages use the common tool-calling shape (role + content blocks: text, tool_use, tool_result), so the same
loop code drives both clients.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

Message = dict[str, Any]
ToolSpec = dict[str, Any]  # {"name", "description", "input_schema": JSON Schema}


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LlmTurn:
    """One model response: optional text and the tool calls it asked for."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The provider's own content blocks, when it needs them echoed back verbatim (thinking blocks); None means the
    # loop rebuilds the assistant turn from `text` and `tool_calls`.
    raw_content: list[Any] | None = None


OnTurn = Callable[[], None]


class LlmClient(Protocol):
    async def complete(self, *, system: str, messages: list[Message], tools: list[ToolSpec]) -> LlmTurn: ...
