"""Per-phase token ceilings enforced on the message history sent to the model.

Phase 0: a no-op passthrough (BudgetTracker in orchestrator/budget.py already
stops a phase once its tool-call/token budget is spent, which is enough for a
single small run). Phase 2 tightens this into a hard trim of old messages
once a phase's ceiling is close, calling into summarizer.py first.
"""
from __future__ import annotations

from harness.model_adapter.base import Message


def enforce_ceiling(messages: list[Message], max_tokens: int) -> list[Message]:
    return messages
