"""The provider-neutral LLM interface the agent loop talks to. The fake and the real client both implement it.

Messages use the common tool-calling shape (role + content blocks: text, tool_use, tool_result), so the same
loop code drives both clients.
"""

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


class LlmClient(Protocol):
    async def complete(self, *, system: str, messages: list[Message], tools: list[ToolSpec]) -> LlmTurn: ...
