"""Cheap triage classifier, run once at the very start of Understand, before
any repo access. Classifies the raw issue text into trivial/moderate/complex
so the orchestrator can size the run's budget proportionally instead of
handing every issue the same fixed allowance (the naive-baseline behavior
Phase 2 exists to beat).
"""
from __future__ import annotations

import json
import re

from harness.model_adapter.base import Message, ModelAdapter, Usage

VALID_TIERS = ("trivial", "moderate", "complex")

_SYSTEM_PROMPT = (
    "You are a fast triage classifier for a software engineering agent. Given a raw "
    "issue description, classify its implementation difficulty as exactly one of: "
    "trivial, moderate, complex. Respond with ONLY a JSON object, no other text: "
    '{"tier": "trivial|moderate|complex", "justification": "<one sentence>"}'
)

_TIER_RE = re.compile(r"\b(trivial|moderate|complex)\b", re.IGNORECASE)


def classify_issue(adapter: ModelAdapter, issue_text: str) -> tuple[str, str, Usage]:
    """Returns (tier, justification, usage). Never raises on a malformed model
    reply -- falls back to a regex scan for a tier keyword, then to "moderate"
    (the safe middle default) if even that fails."""
    messages = [
        Message(role="system", content=_SYSTEM_PROMPT),
        Message(role="user", content=issue_text),
    ]
    resp = adapter.complete(messages, tools=[], max_tokens=150)
    tier, justification = _parse(resp.content)
    return tier, justification, resp.usage


def _parse(content: str) -> tuple[str, str]:
    try:
        obj = json.loads(content)
        tier = str(obj.get("tier", "")).lower()
        if tier in VALID_TIERS:
            return tier, str(obj.get("justification", "")).strip() or "(no justification given)"
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    m = _TIER_RE.search(content)
    if m:
        return m.group(1).lower(), content.strip() or "(unstructured triage reply, tier extracted by keyword match)"

    return "moderate", "(triage reply unparseable; defaulted to moderate)"
