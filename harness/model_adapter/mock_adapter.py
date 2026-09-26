"""Deterministic scripted adapter. Same ModelAdapter interface as LiteLLMAdapter,
so it drops into the orchestrator without any special-casing. Used for demo
reliability when no live API key is configured yet -- swap to LiteLLMAdapter
by changing one line of config once a key (e.g. GROQ_API_KEY) is set."""
from __future__ import annotations

from typing import Any

from .base import Message, ModelResponse, ToolCall, Usage


class MockAdapter:
    """Replays a fixed script of (content, tool_call) steps, one per .complete() call.

    Each step is a dict: {"content": str, "tool": {"name": str, "arguments": dict}}
    "tool" may be omitted for a plain assistant message (no tool call).
    """

    def __init__(self, script: list[dict[str, Any]]):
        self.script = script
        self._i = 0

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        if self._i >= len(self.script):
            return ModelResponse(content="(mock script exhausted)", tool_calls=[], usage=Usage())
        step = self.script[self._i]
        self._i += 1

        tool_calls: list[ToolCall] = []
        if "tool" in step:
            tool_calls.append(
                ToolCall(id=f"mock-{self._i}", name=step["tool"]["name"], arguments=step["tool"]["arguments"])
            )
        prompt_tokens = sum(len(m.content) for m in messages) // 4
        completion_tokens = len(step.get("content", "")) // 4 + 20
        return ModelResponse(
            content=step.get("content", ""),
            tool_calls=tool_calls,
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        )
