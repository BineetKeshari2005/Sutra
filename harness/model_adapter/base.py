"""Model-agnostic adapter protocol. Every LLM call in the harness goes through this."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # set on role="tool" replies
    name: str | None = None  # tool name, set on role="tool" replies

    def to_openai_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": _json_dumps(tc.arguments)},
                }
                for tc in self.tool_calls
            ]
        if self.role == "tool":
            d["tool_call_id"] = self.tool_call_id
            d["name"] = self.name
        return d


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ModelResponse:
    content: str
    tool_calls: list[ToolCall]
    usage: Usage
    raw: Any = None


class ModelAdapter(Protocol):
    """A swappable interface over any chat-completions-with-tools model."""

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        ...


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj)
