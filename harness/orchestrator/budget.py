"""Per-phase token/tool-call budget accounting.

Phase 0: flat default budgets for every run. Phase 2 adds a triage step that
sets these per-issue-complexity instead of using PHASE_DEFAULTS unmodified --
that's why `set_budget` exists as a seam rather than budgets being hardcoded
into BudgetTracker itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PHASES = ["understand", "localize", "plan", "act", "verify", "reflect", "finalize"]


@dataclass
class PhaseBudget:
    max_tool_calls: int
    max_tokens: int


PHASE_DEFAULTS: dict[str, PhaseBudget] = {
    "understand": PhaseBudget(max_tool_calls=3, max_tokens=8_000),
    "localize": PhaseBudget(max_tool_calls=8, max_tokens=16_000),
    "plan": PhaseBudget(max_tool_calls=2, max_tokens=6_000),
    "act": PhaseBudget(max_tool_calls=15, max_tokens=30_000),
    "verify": PhaseBudget(max_tool_calls=4, max_tokens=8_000),
    "reflect": PhaseBudget(max_tool_calls=2, max_tokens=6_000),
    "finalize": PhaseBudget(max_tool_calls=2, max_tokens=4_000),
}


@dataclass
class PhaseSpend:
    tool_calls: int = 0
    tokens: int = 0


class BudgetTracker:
    def __init__(self, budgets: dict[str, PhaseBudget] | None = None):
        self.budgets = budgets or {k: PhaseBudget(v.max_tool_calls, v.max_tokens) for k, v in PHASE_DEFAULTS.items()}
        self.spend: dict[str, PhaseSpend] = {p: PhaseSpend() for p in PHASES}

    def set_budget(self, phase: str, max_tool_calls: int, max_tokens: int) -> None:
        self.budgets[phase] = PhaseBudget(max_tool_calls, max_tokens)

    def record(self, phase: str, tool_calls: int = 0, tokens: int = 0) -> None:
        s = self.spend[phase]
        s.tool_calls += tool_calls
        s.tokens += tokens

    def exhausted(self, phase: str) -> bool:
        b, s = self.budgets[phase], self.spend[phase]
        return s.tool_calls >= b.max_tool_calls or s.tokens >= b.max_tokens

    def remaining_tool_calls(self, phase: str) -> int:
        return max(0, self.budgets[phase].max_tool_calls - self.spend[phase].tool_calls)

    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.spend.values())

    def total_tool_calls(self) -> int:
        return sum(s.tool_calls for s in self.spend.values())

    def as_dict(self) -> dict:
        return {
            p: {
                "budget_tool_calls": self.budgets[p].max_tool_calls,
                "budget_tokens": self.budgets[p].max_tokens,
                "spent_tool_calls": self.spend[p].tool_calls,
                "spent_tokens": self.spend[p].tokens,
            }
            for p in PHASES
        }
