"""Rolling summarization of old turns using the run's own model, to keep long
Act/Localize tool-call loops from having every large tool observation
(e.g. an open_file dump of a real, sizeable file) sit verbatim in every later
prompt for the rest of the phase. Wired into harness/context_manager/budgeter.py's
enforce_ceiling, which decides *when* to call this; this module only does the
LLM call itself."""
from __future__ import annotations

from harness.model_adapter.base import Message, ModelAdapter, Usage


def summarize_messages(messages: list[Message], adapter: ModelAdapter) -> tuple[Message, Usage]:
    joined = "\n".join(f"[{m.role}] {m.content[:500]}" for m in messages)
    prompt = [
        Message(role="system", content="Summarize this agent trajectory excerpt in under 150 words, preserving any file paths, function names, and error messages."),
        Message(role="user", content=joined),
    ]
    resp = adapter.complete(prompt, tools=[], max_tokens=300)
    summary = Message(role="assistant", content=f"[summary of {len(messages)} earlier turns] {resp.content}")
    return summary, resp.usage
