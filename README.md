# Sutra-AI: autonomous coding-agent harness

An autonomous SWE-agent harness around a model-agnostic LLM adapter, built as a
minimal-descendant of mini-SWE-agent's phase-loop architecture, with three
differentiators layered on top (scrubbable trajectory timeline, complexity-aware
budget routing, repo-level institutional memory).

## Status: Phase 0 + Phase 1 + Phase 2 complete

The orchestrator, five core tools, model adapter, sandbox, and verifier gates
are wired end-to-end and have solved a real bug (more-itertools' `one()`/`only()`
falsy-exception bug, upstream fix `def2dab`) in a fresh sandboxed clone,
verified by the repo's own pytest suite -- including a genuine
failure -> reflection -> retry cycle (not scripted: the first attempt only
fixed `one()`, the regression gate caught that `only()` was still broken, and
the second attempt fixed it).

```
VERIFIED: True
  [PASS] patch_applies
  [PASS] builds
  [PASS] target_test_passes
  [PASS] regression_subset_passes
retries_used=1
```

## Running the Phase 0 demo

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python demo/run_demo.py
```

By default this runs against a scripted `MockAdapter` (deterministic replay --
no API key, no live cost) so the harness mechanics can be verified reliably.
Set `GROQ_API_KEY` (see `.env.example`) to switch to a live model via
`LiteLLMAdapter(model="groq/llama-3.3-70b-versatile")` -- no other code changes
needed, since every LLM call goes through the same `ModelAdapter` interface.

## Trajectory timeline UI (Phase 1)

```bash
python demo/server.py
# open http://127.0.0.1:8008
```

Loads `demo/fixtures/more-itertools-trajectory.jsonl` by default -- a locked
copy of the exact Phase 0 run above -- so the demo is fully offline and has
zero dependency on a live model call succeeding at demo time. Three panels:
a phase rail (Understand -> ... -> Finalize) that highlights as you scrub, an
expandable event timeline with the failure -> reflection -> retry sequence
called out in an amber "self-correction" block, and a sidebar with running
token/tool-call counts and a live per-gate pass/fail breakdown. `GET
/api/trajectory/live` will also tail a real `demo/trajectory.jsonl` from an
in-progress run, if present.

## Complexity-aware budgets + cost dashboard (Phase 2)

A cheap triage call classifies the issue as `trivial`/`moderate`/`complex`
*before* any repo access happens, and sizes each phase's token/tool-call
budget proportionally (`BUDGET_PROFILES` in `harness/orchestrator/budget.py`).
verify/finalize's share is carved out first and is non-negotiable, so Act can
never spend the whole budget and leave nothing to verify with.

This isn't just a logged number: the orchestrator checks remaining budget
before every tool call, and force-transitions a phase (e.g. cutting Act short
into Verify) the moment its slice is exhausted, logging a `budget_enforced`
event. `tests/test_budget_enforcement.py` starves a run's budget on purpose
and asserts the cutoff actually happens mid-Act.

```bash
python demo/run_demo.py                  # triage picks a budget tier
python demo/run_demo.py --naive-baseline  # control: skip triage, always use
                                            # the largest profile, never force
                                            # an early transition
```

Both reach the identical verified patch. The naive run isn't cut off (naive
disables enforcement outright), but doing the work without a triage-sized
budget or windowed reads costs real, measured tokens -- **5.8x more** in the
committed fixtures: 12,311 (Sutra) vs. 71,823 (naive baseline). The timeline
UI's sidebar shows this as a two-bar chart plus the tier + justification for
whichever run is loaded, reading `demo/fixtures/more-itertools-trajectory.jsonl`
and `demo/fixtures/more-itertools-naive-baseline.jsonl` with zero live
dependency.

Run the harness's own test suite:

```bash
python -m pytest tests/ -q
```

## Architecture

```
harness/
  orchestrator/
    state_machine.py   # Understand -> Localize -> Plan -> Act -> Verify -> Reflect -> Finalize
    budget.py           # BUDGET_PROFILES (trivial/moderate/complex) + BudgetTracker
    triage.py            # cheap classifier: issue text -> tier + justification
    tool_registry.py       # tool schemas + dispatch table
  model_adapter/
    base.py               # ModelAdapter protocol (Message, ToolCall, ModelResponse)
    litellm_adapter.py      # live adapter (Claude / GPT / Groq / any litellm-supported model)
    mock_adapter.py           # scripted adapter for deterministic demo runs
  tools/
    search.py                  # search_code (ripgrep or pure-Python fallback), search_symbol
    file_ops.py                  # open_file (windowed), edit_file (exact-match, hint-bearing errors)
    test_runner.py                 # run_tests -> structured, truncated pytest output
    git_ops.py                       # git_diff / git_checkpoint / git_reset
    bash.py                            # sandboxed escape-hatch shell (rlimits + timeout)
  context_manager/                     # per-phase context ceilings + rolling summarization (Phase 2)
  memory/
    trajectory_store.py                  # JSONL event log (Phase 1 timeline UI reads this)
    repo_memory.py                         # per-repo institutional memory (Phase 3)
  verifier/
    gates.py                                 # patch-applies -> builds -> target test -> regression subset
  sandbox/
    local_sandbox.py                           # subprocess + tempdir clone (active fallback)
    docker_sandbox.py                            # container-per-run (not a hard dependency)
  eval/
    run_swebench.py                                # SWE-bench-style scoring harness
demo/
  run_demo.py         # driver: prepares sandbox, runs one issue end-to-end;
                       # --naive-baseline for the Phase 2 control run
  server.py            # FastAPI: serves fixture/live trajectory JSON + the UI
  fixtures/             # locked known-good trajectory JSONL(s) for offline demos
  ui/                    # scrubbable timeline + cost dashboard (Phase 1/2)
tests/                     # harness's own pytest suite
```

**Verification is never done by parsing the model's chat reply for a diff.**
The agent edits files in place inside a sandboxed clone; `git diff` against the
base commit at submission time is the only source of truth for the patch.

## Next

- Phase 3: `repo_memory.py` -- solve two issues in the same repo back-to-back
  and show the second run getting faster/cheaper from what the first wrote back.
