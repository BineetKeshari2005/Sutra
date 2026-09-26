"""Calibrated abstention: when a run exhausts its retry allowance without all
gates passing, Finalize must never submit the broken patch as if it were done,
and never submit silence either. This generates the structured confidence
report that goes out instead, alongside the best checkpoint reached.
"""
from __future__ import annotations

import json

from harness.model_adapter.base import Message, ModelAdapter, Usage

_SYSTEM_PROMPT = (
    "An autonomous SWE-agent run did not fully pass verification within its retry "
    "budget. Read the trajectory summary below and produce a calibrated confidence "
    "report -- honest uncertainty, not a guess dressed up as fact. Respond with ONLY "
    "a JSON object, no other text:\n"
    '{"root_cause_confidence": "high|medium|low", '
    '"root_cause_summary": "one or two plain-language sentences", '
    '"unresolved_issue": "one or two sentences: specifically what is still broken and '
    'why the agent could not close it within budget"}'
)


def generate_confidence_report(adapter: ModelAdapter, trajectory_summary: str, attempts_made: int) -> tuple[dict, Usage]:
    messages = [
        Message(role="system", content=_SYSTEM_PROMPT),
        Message(role="user", content=trajectory_summary),
    ]
    resp = adapter.complete(messages, tools=[], max_tokens=250)
    report = _parse(resp.content)
    report["attempts_made"] = attempts_made
    return report, resp.usage


def _parse(content: str) -> dict:
    try:
        obj = json.loads(content)
        return {
            "root_cause_confidence": obj.get("root_cause_confidence", "low"),
            "root_cause_summary": obj.get("root_cause_summary", "(no summary given)"),
            "unresolved_issue": obj.get("unresolved_issue", "(unspecified)"),
        }
    except (json.JSONDecodeError, TypeError, AttributeError):
        return {
            "root_cause_confidence": "low",
            "root_cause_summary": "(confidence report generation failed to parse)",
            "unresolved_issue": "(unspecified)",
        }
