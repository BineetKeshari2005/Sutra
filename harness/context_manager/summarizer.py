"""Rolling summarization of old turns using a cheap model, to keep long ACT-phase
loops from blowing the context window. Not exercised in Phase 0 (single small
run stays well under any real context limit); wired in once budgeter.py starts
trimming history in Phase 2."""
from __future__ import annotations

from harness.model_adapter.base import Message, ModelAdapter


def summarize_messages(messages: list[Message], adapter: ModelAdapter) -> Message:
    joined = "\n".join(f"[{m.role}] {m.content[:500]}" for m in messages)
    prompt = [
        Message(role="system", content="Summarize this agent trajectory excerpt in under 150 words, preserving any file paths, function names, and error messages."),
        Message(role="user", content=joined),
    ]
    resp = adapter.complete(prompt, tools=[], max_tokens=300)
    return Message(role="assistant", content=f"[summary of {len(messages)} earlier turns] {resp.content}")
