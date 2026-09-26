# Sutra-AI: autonomous coding-agent harness

An autonomous SWE-agent harness around a model-agnostic LLM adapter, built as a
minimal-descendant of mini-SWE-agent's phase-loop architecture, with three
differentiators layered on top (scrubbable trajectory timeline, complexity-aware
budget routing, repo-level institutional memory).

## Status: Phase 0 + Phase 1 + Phase 2 + Phase 3 complete

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
python demo/run_demo.py --issue 1
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

## Repo-level institutional memory (Phase 3)

Every run reads `harness/memory/repo_memory.py`'s per-repo JSON file (keyed by
the repo's root commit hash, stable across every ephemeral sandbox clone) at
the start of Localize, and writes back to it at Finalize regardless of
pass/fail -- a failed attempt's landmine is often the single most valuable
thing to remember.

```bash
python demo/run_demo.py --issue 1 --reset-memory  # fresh, empty memory bank
python demo/run_demo.py --issue 2                  # same repo, memory populated by issue #1
```

Issue #1 (the `one()`/`only()` bug above) and issue #2 (`constrained_batches()`
accepting a nonpositive `max_count` -- another real upstream bug, fix `d032cab`)
are both cherry-picked onto the *same* shared base commit, so
`more_itertools/more.py` is byte-identical going into either run. That's what
makes the payoff real rather than staged: issue #1's Finalize step builds a
full top-level symbol index for the whole file (not just the function it
touched) and caches it fingerprinted to that exact file content; issue #2's
Localize step hits that cache (content unchanged) and skips search entirely.

Measured, not assumed -- issue #2 needs **zero** localize tool calls (vs. 1 on
issue #1) and **42% fewer tokens overall** (8,046 vs. 13,843), while landing
the identical real upstream patch. The timeline UI's sidebar shows this as a
side-by-side chart plus the exact notes/cache issue #2 started with, and the
moment they were read and acted on is highlighted in the timeline itself,
the same treatment Phase 1 gives the self-correction sequence.

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
  context_manager/                     # per-phase context ceilings + rolling summarization
  memory/
    trajectory_store.py                  # JSONL event log (Phase 1 timeline UI reads this)
    repo_memory.py                         # per-repo JSON store: conventions/landmines/
                                             # fix_patterns + symbol index cache (Phase 3)
    memory_writer.py                         # LLM call at Finalize: trajectory -> memory entries
  verifier/
    gates.py                                 # patch-applies -> builds -> target test -> regression subset
  sandbox/
    local_sandbox.py                           # subprocess + tempdir clone (active fallback)
    docker_sandbox.py                            # container-per-run (not a hard dependency)
  eval/
    run_swebench.py                                # SWE-bench-style scoring harness
demo/
  run_demo.py         # driver: --issue {1,2}, --naive-baseline, --reset-memory
  server.py            # FastAPI: serves fixture/live trajectory JSON + the UI
  fixtures/             # locked known-good trajectory JSONL(s) for offline demos
  ui/                    # scrubbable timeline + cost dashboard + memory comparison
tests/                     # harness's own pytest suite
```

**Verification is never done by parsing the model's chat reply for a diff.**
The agent edits files in place inside a sandboxed clone; `git diff` against the
base commit at submission time is the only source of truth for the patch.

## Next

- Add-ons (time permitting): calibrated abstention (submit best checkpoint +
  a confidence report instead of a falsely-confident patch when verification
  doesn't fully pass) and blind adversarial review (a second model call sees
  only the issue text + final diff, no reasoning trace, and flags anything
  that looks like it's gaming the tests).
