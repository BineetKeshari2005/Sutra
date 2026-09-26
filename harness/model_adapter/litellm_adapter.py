"""Concrete ModelAdapter backed by litellm, so the harness can target Claude, GPT,
Groq-hosted open-weight models, etc. purely via config (model name + env var)."""
from __future__ import annotations

import json
import os
from typing import Any

from .base import Message, ModelResponse, ToolCall, Usage

_DOTENV_LOADED = False


def _load_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class LiteLLMAdapter:
    """Wraps litellm.completion. Model string selects the provider,
    e.g. "groq/llama-3.3-70b-versatile", "claude-sonnet-5", "gpt-4.1-mini"."""

    def __init__(self, model: str, api_key_env: str | None = None):
        _load_dotenv_once()
        self.model = model
        if api_key_env and not os.environ.get(api_key_env):
            raise RuntimeError(
                f"LiteLLMAdapter requires {api_key_env} to be set (env var or .env file) "
                f"to call model '{model}'."
            )

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        import litellm

        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=[m.to_openai_dict() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = litellm.completion(**kwargs)
        choice = resp.choices[0].message

        tool_calls: list[ToolCall] = []
        for tc in getattr(choice, "tool_calls", None) or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw_arguments": tc.function.arguments}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        usage = Usage(
            prompt_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
        )
        return ModelResponse(
            content=choice.content or "",
            tool_calls=tool_calls,
            usage=usage,
            raw=resp,
        )
