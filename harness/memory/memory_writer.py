"""One model call at Finalize that reads a compact trajectory summary and
extracts a small, selective set of memory entries -- explicitly capped at a
few short notes, since junk entries poison future runs' context budget for
no benefit. Runs regardless of whether the run passed verification: a
failed attempt's landmine is often the single most valuable thing to
remember.
"""
from __future__ import annotations

import json

from harness.model_adapter.base import Message, ModelAdapter, Usage

_SYSTEM_PROMPT = (
    "You just finished an autonomous SWE-agent run on a repository. Read the trajectory "
    "summary below and extract AT MOST 3 short, high-value notes for whoever works on this "
    "repo next. Be selective: 1-3 entries total across all categories, not a wall of notes. "
    "Each note should read like a sentence from a colleague, not a structured taxonomy.\n"
    "Categories:\n"
    "- conventions: a coding/testing pattern worth knowing (e.g. how tests are organized)\n"
    "- landmines: a specific function/file that caused trouble, and exactly why -- these are "
    "valuable even (especially) when the run failed or needed a retry\n"
    "- fix_patterns: a reusable shape of bug, generalizable beyond this one function\n"
    "Respond with ONLY a JSON object, no other text:\n"
    '{"conventions": [{"note": "..."}], '
    '"landmines": [{"path": "...", "symbol": "...", "note": "..."}], '
    '"fix_patterns": [{"pattern": "...", "example_diff_summary": "..."}]}'
)


def extract_memory_entries(adapter: ModelAdapter, trajectory_summary: str) -> tuple[dict[str, list[dict]], Usage]:
    messages = [
        Message(role="system", content=_SYSTEM_PROMPT),
        Message(role="user", content=trajectory_summary),
    ]
    resp = adapter.complete(messages, tools=[], max_tokens=400)
    return _parse(resp.content), resp.usage


def _parse(content: str) -> dict[str, list[dict]]:
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {"conventions": [], "landmines": [], "fix_patterns": []}
    return {
        "conventions": obj.get("conventions", []) or [],
        "landmines": obj.get("landmines", []) or [],
        "fix_patterns": obj.get("fix_patterns", []) or [],
    }
