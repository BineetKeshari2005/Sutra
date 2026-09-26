"""The orchestrator: a straightforward phase state machine.

Understand -> Localize -> Plan -> Act -> Verify -> Reflect -> Finalize

One tool call per model turn. Every successful edit_file gets an immediate
git checkpoint commit, so trajectory and repo history always line up and a
failed later step can be diffed against a known-good point.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from harness.model_adapter.base import Message, ModelAdapter
from harness.memory.memory_writer import extract_memory_entries
from harness.memory.repo_memory import RepoMemory, repo_id_for
from harness.memory.trajectory_store import TrajectoryStore
from harness.orchestrator.budget import BudgetTracker, build_budget_tracker
from harness.orchestrator.tool_registry import TOOL_SCHEMAS, make_dispatch
from harness.orchestrator.triage import classify_issue
from harness.tools import git_ops
from harness.verifier import gates as verifier_gates

MAX_REFLECT_RETRIES = 1
SAFETY_MAX_ITERS_PER_PHASE = 25
NAIVE_BASELINE_TIER = "complex"


@dataclass
class RunConfig:
    repo_path: str
    base_commit: str
    issue_text: str
    target_test: str | None = None
    regression_test_paths: list[str] | None = None
    issue_id: str = "unknown-issue"


@dataclass
class RunResult:
    verified: bool
    gates: dict[str, Any]
    diff: str
    total_tokens: int
    total_tool_calls: int
    retries_used: int
    tier: str
    triage_justification: str
    confidence_report: dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self,
        adapter: ModelAdapter,
        config: RunConfig,
        trajectory: TrajectoryStore,
        naive_baseline: bool = False,
        repo_memory: RepoMemory | None = None,
    ):
        self.adapter = adapter
        self.config = config
        self.trajectory = trajectory
        self.naive_baseline = naive_baseline
        self.enforce_budget = not naive_baseline
        self.budget: BudgetTracker | None = None  # set by _triage() at the start of run()
        self.tier = ""
        self.triage_justification = ""
        self.dispatch = make_dispatch(config.repo_path)
        self.repo_memory = repo_memory or RepoMemory(repo_id_for(config.repo_path))
        self._touched_paths: set[str] = set()
        self.messages: list[Message] = [
            Message(
                role="system",
                content=(
                    "You are an autonomous software engineering agent fixing a real bug "
                    "in the repository checked out at the sandbox path. Use the provided "
                    "tools; never guess at file contents you haven't read. Make exactly "
                    "one tool call per turn."
                ),
            ),
            Message(role="user", content=f"Issue:\n{config.issue_text}"),
        ]

    # ---- phase helpers -------------------------------------------------

    def _log(self, phase: str, type_: str, payload: dict[str, Any] | None = None, tokens: int = 0) -> None:
        self.trajectory.append(phase, type_, payload, tokens)

    def _log_budget_enforced(self, phase: str, reason: str) -> None:
        self._log(phase, "budget_enforced", {"reason": reason, "budget": self.budget.as_dict()[phase]})

    def _triage(self) -> None:
        if self.naive_baseline:
            self.tier = NAIVE_BASELINE_TIER
            self.triage_justification = "(naive baseline: triage classifier skipped, always uses the largest budget profile)"
            self.budget = build_budget_tracker(self.tier)
            self._log(
                "understand",
                "triage",
                {"tier": self.tier, "justification": self.triage_justification, "budget_profile": self.budget.as_dict(), "skipped": True},
            )
            return

        tier, justification, usage = classify_issue(self.adapter, self.config.issue_text)
        self.tier = tier
        self.triage_justification = justification
        self.budget = build_budget_tracker(tier)
        self.budget.record("understand", tokens=usage.total_tokens)
        self._log(
            "understand",
            "triage",
            {"tier": tier, "justification": justification, "budget_profile": self.budget.as_dict()},
            tokens=usage.total_tokens,
        )

    def _memory_read(self) -> None:
        """Queried at the start of Localize, before any fresh search happens."""
        relevant = self.repo_memory.query_relevant(self.config.issue_text)
        cache_hits = self.repo_memory.all_fresh_symbol_indexes(self.config.repo_path)

        if not relevant and not cache_hits:
            self._log("localize", "memory_read", {"hit": False, "entries": [], "cache_hits": {}})
            return

        lines = []
        if relevant:
            lines.append("Prior notes on this repo (from repo memory):")
            for e in relevant:
                lines.append(f"- [{e['category']}] {e.get('note') or e.get('pattern')}")
        if cache_hits:
            lines.append(
                "Cached symbol index available for these files (fresh -- unchanged since last "
                "indexed); no fresh search needed if it already covers what you're looking for:"
            )
            for path, index in cache_hits.items():
                preview = ", ".join(f"{name}:{line}" for name, line in index.items())
                lines.append(f"- {path}: {{{preview}}}")
        note_text = "\n".join(lines)

        self.messages.append(Message(role="user", content=note_text))
        self._log("localize", "memory_read", {"hit": True, "entries": relevant, "cache_hits": cache_hits, "note_text": note_text})

    def _memory_write(self, verified: bool) -> None:
        """Runs at Finalize regardless of pass/fail -- a failed attempt's
        landmine is often the single most valuable thing to remember."""
        transcript_tail = [m for m in self.messages if m.role in ("assistant", "tool") and m.content]
        summary = "\n".join(f"[{m.role}] {m.content[:300]}" for m in transcript_tail[-40:])
        trajectory_summary = f"Issue: {self.config.issue_text[:500]}\nVerified: {verified}\n\n{summary}"

        entries, usage = extract_memory_entries(self.adapter, trajectory_summary)
        self.budget.record("finalize", tokens=usage.total_tokens)

        written: dict[str, list[dict]] = {}
        for category, items in entries.items():
            added = self.repo_memory.add_entries(category, items, learned_from_issue=self.config.issue_id)
            if added:
                written[category] = added

        indexed_files: dict[str, int] = {}
        for path in self._touched_paths:
            pristine = git_ops.show_file_at_commit(self.config.repo_path, self.config.base_commit, path)
            if pristine is not None:
                index = self.repo_memory.update_symbol_index(path, pristine)
                indexed_files[path] = len(index)

        self._log(
            "finalize",
            "memory_write",
            {"entries_written": written, "symbol_index_updated": indexed_files, "repo_id": self.repo_memory.repo_id},
            tokens=usage.total_tokens,
        )

    def _single_turn(self, phase: str, instruction: str) -> str:
        self._log(phase, "phase_start", {"instruction": instruction})
        self.messages.append(Message(role="user", content=instruction))
        resp = self.adapter.complete(self.messages, tools=[])
        self.budget.record(phase, tool_calls=0, tokens=resp.usage.total_tokens)
        self.messages.append(Message(role="assistant", content=resp.content))
        self._log(phase, "model_call", {"content": resp.content}, tokens=resp.usage.total_tokens)
        return resp.content

    def _tool_loop(self, phase: str, instruction: str) -> None:
        self._log(phase, "phase_start", {"instruction": instruction})
        self.messages.append(Message(role="user", content=instruction))

        for _ in range(SAFETY_MAX_ITERS_PER_PHASE):
            # Checked before every model call, not just logged after the fact:
            # a phase whose budget is already spent is force-transitioned out
            # rather than allowed to keep going.
            if self.enforce_budget and self.budget.exhausted(phase):
                self._log_budget_enforced(phase, "phase budget exhausted before next model call -- forcing phase transition")
                break

            resp = self.adapter.complete(self.messages, tools=TOOL_SCHEMAS)
            self.budget.record(phase, tool_calls=0, tokens=resp.usage.total_tokens)
            self._log(phase, "model_call", {"content": resp.content}, tokens=resp.usage.total_tokens)

            if not resp.tool_calls:
                self.messages.append(Message(role="assistant", content=resp.content))
                break  # model signaled it's done with this phase

            call = resp.tool_calls[0]  # exactly one tool call per turn
            self.messages.append(Message(role="assistant", content=resp.content, tool_calls=[call]))

            # Checked again right before executing the tool the model just
            # requested: this model call's own tokens may have crossed the
            # ceiling, and the tool must not run once that's true.
            if self.enforce_budget and self.budget.exhausted(phase):
                self._log_budget_enforced(phase, f"budget exhausted -- refusing to execute {call.name}, forcing phase transition")
                observation = {
                    "ok": False,
                    "error": "phase budget exhausted",
                    "hint": "the phase is ending now; proceed to verification with the current diff",
                }
                self.messages.append(
                    Message(role="tool", content=json.dumps(observation), tool_call_id=call.id, name=call.name)
                )
                break

            self.budget.record(phase, tool_calls=1)

            if call.name in ("open_file", "edit_file") and "path" in call.arguments:
                self._touched_paths.add(call.arguments["path"])

            self._log(phase, "tool_call", {"name": call.name, "arguments": call.arguments})
            observation = self._execute_tool(call.name, call.arguments)
            self._log(phase, "observation", {"name": call.name, "result": observation})

            if call.name == "edit_file" and observation.get("ok"):
                ckpt = git_ops.git_checkpoint(self.config.repo_path, f"agent: edit {call.arguments.get('path')}")
                self._log(phase, "checkpoint", ckpt)

            self.messages.append(
                Message(role="tool", content=json.dumps(observation), tool_call_id=call.id, name=call.name)
            )

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        fn = self.dispatch.get(name)
        if fn is None:
            return {"ok": False, "error": f"unknown tool '{name}'", "hint": "use one of the tools provided in the schema"}
        try:
            return fn(**arguments)
        except TypeError as e:
            return {"ok": False, "error": f"bad arguments for {name}: {e}", "hint": "check the tool's required parameters"}

    # ---- main run loop ---------------------------------------------------

    def run(self) -> RunResult:
        self._triage()

        self._single_turn(
            "understand",
            "Read the issue above. In 2-4 sentences, restate the bug and what a correct fix must do. Do not use tools yet.",
        )

        self._memory_read()

        self._tool_loop(
            "localize",
            "Use search_code / search_symbol / open_file to locate the exact function(s) responsible for this bug. "
            "If the repo-memory notes above already give you the file/line, you don't need to search again. "
            "When you've found it, reply with a short summary and no further tool call.",
        )

        self._single_turn(
            "plan",
            "State a concise, concrete plan: which file(s) and function(s) you will edit, and the exact change. No tools.",
        )

        retries = 0
        gate_result: dict[str, Any] = {"verified": False, "gates": {}}
        while True:
            self._tool_loop(
                "act",
                "Make the planned edit(s) with edit_file, then call run_tests to check your fix. "
                "Reply with no tool call once you believe the fix is complete and tested.",
            )

            self._log("verify", "phase_start", {})
            gate_result = verifier_gates.run_gates(
                self.config.repo_path,
                self.config.base_commit,
                self.config.target_test,
                self.config.regression_test_paths,
            )
            self._log("verify", "verify_gate", gate_result)

            if gate_result["verified"] or retries >= MAX_REFLECT_RETRIES:
                break

            retries += 1
            failure_summary = "; ".join(
                f"{name}: {g['detail']}" for name, g in gate_result["gates"].items() if not g["passed"]
            )
            self._single_turn(
                "reflect",
                f"Verification failed: {failure_summary}\n"
                "Diagnose the root cause in 2-3 sentences, then state what you'll change next. No tools.",
            )

        diff = git_ops.git_diff(self.config.repo_path, base_ref=self.config.base_commit)
        git_ops.git_checkpoint(self.config.repo_path, "agent: final checkpoint")

        self._memory_write(gate_result["verified"])

        confidence_report = {
            "gates_passed": sum(1 for g in gate_result["gates"].values() if g["passed"]),
            "gates_total": len(gate_result["gates"]),
            "verified": gate_result["verified"],
            "retries_used": retries,
            "tier": self.tier,
        }
        self._log("finalize", "run_end", {"confidence_report": confidence_report, "diff_lines": len(diff.get("diff", "").splitlines())})

        return RunResult(
            verified=gate_result["verified"],
            gates=gate_result["gates"],
            diff=diff.get("diff", ""),
            total_tokens=self.budget.total_tokens(),
            total_tool_calls=self.budget.total_tool_calls(),
            retries_used=retries,
            tier=self.tier,
            triage_justification=self.triage_justification,
            confidence_report=confidence_report,
        )
