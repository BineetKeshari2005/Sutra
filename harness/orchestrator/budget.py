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


# Complexity-tier budget profiles (Phase 2). `phase_split` fractions must sum to
# 1.0; verify/finalize's shares are carved out first and are non-negotiable --
# `build_budget_tracker` allocates them before "act" absorbs the remainder, so
# a big Act phase can never crowd verification out of tokens/tool-calls to run
# with (in practice verify/finalize don't call the model at all today, but the
# reservation is what stops Act from being handed the entire budget outright).
PHASE_SPLIT: dict[str, float] = {
    "understand": 0.05,
    "localize": 0.15,
    "plan": 0.05,
    "act": 0.55,
    "verify": 0.05,
    "reflect": 0.10,
    "finalize": 0.05,
}

BUDGET_PROFILES: dict[str, dict] = {
    "trivial": {"total_tokens": 8_000, "max_tool_calls": 15, "phase_split": PHASE_SPLIT},
    "moderate": {"total_tokens": 25_000, "max_tool_calls": 40, "phase_split": PHASE_SPLIT},
    "complex": {"total_tokens": 60_000, "max_tool_calls": 90, "phase_split": PHASE_SPLIT},
}


@dataclass
class PhaseSpend:
    tool_calls: int = 0
    tokens: int = 0


class BudgetTracker:
    def __init__(self, budgets: dict[str, PhaseBudget] | None = None, tier: str | None = None):
        self.budgets = budgets or {k: PhaseBudget(v.max_tool_calls, v.max_tokens) for k, v in PHASE_DEFAULTS.items()}
        self.spend: dict[str, PhaseSpend] = {p: PhaseSpend() for p in PHASES}
        self.tier = tier

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


def build_budget_tracker(tier: str) -> BudgetTracker:
    """Resolve a tier ("trivial"/"moderate"/"complex") into a BudgetTracker
    with per-phase allocations. verify/finalize/understand/localize/plan/reflect
    get their exact phase_split share of the tier's totals (their reservation
    is carved out first); "act" absorbs whatever's left, so rounding never
    shrinks the non-negotiable phases."""
    if tier not in BUDGET_PROFILES:
        raise ValueError(f"unknown budget tier '{tier}'; expected one of {list(BUDGET_PROFILES)}")

    profile = BUDGET_PROFILES[tier]
    total_tokens, total_calls, split = profile["total_tokens"], profile["max_tool_calls"], profile["phase_split"]

    budgets: dict[str, PhaseBudget] = {}
    allocated_tokens = allocated_calls = 0
    for phase in PHASES:
        if phase == "act":
            continue
        tok = max(1, round(total_tokens * split[phase]))
        calls = max(1, round(total_calls * split[phase]))
        budgets[phase] = PhaseBudget(max_tool_calls=calls, max_tokens=tok)
        allocated_tokens += tok
        allocated_calls += calls

    budgets["act"] = PhaseBudget(
        max_tool_calls=max(1, total_calls - allocated_calls),
        max_tokens=max(1, total_tokens - allocated_tokens),
    )
    return BudgetTracker(budgets=budgets, tier=tier)
