"""Demo driver for the more-itertools repo: two real issues, sharing one
sandboxed clone family and one repo-memory bank, so Phase 3's institutional
memory can be demonstrated honestly.

Issue #1: one()/only() drop a falsy custom too_long/too_short exception
           (real upstream fix: def2dab).
Issue #2: constrained_batches() doesn't validate a nonpositive max_count
           (real upstream fix: d032cab, test added in 87d1257).

Both are cherry-picked (test-commit only) onto the SAME shared base commit
(19ddb972..., an ancestor of both real fixes) so more_itertools/more.py is
byte-identical going into either run -- which is what makes the repo-memory
symbol-index-cache fingerprint check in Part C a genuine hit on issue #2
rather than a coincidence.

No live model calls are required (MockAdapter replays a scripted trajectory
per issue), but the orchestrator, tools, sandbox, budget tracker, verifier
gates, and repo memory are all exercised for real. Set GROQ_API_KEY to run
live instead -- no other code changes needed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.memory.repo_memory import STORE_DIR, repo_id_for
from harness.memory.trajectory_store import TrajectoryStore
from harness.model_adapter.mock_adapter import MockAdapter
from harness.orchestrator.state_machine import Orchestrator, RunConfig
from harness.sandbox.local_sandbox import LocalSandbox

REPO_LOCAL_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scratch_repos", "more-itertools")

# Ancestor of BOTH real fixes below, so more_itertools/more.py is byte-identical
# going into either issue's sandbox -- required for the symbol-index cache's
# content fingerprint to genuinely match across issue #1 and issue #2.
SHARED_BASE_COMMIT = "19ddb972845ab0e5b9b7449d3fd5930781407441"

ISSUE1_FIX_COMMIT = "def2dabea858b6ecb84ee0c52e6e07929f2c409c"  # one()/only(), test+fix combined
ISSUE2_TEST_COMMIT = "87d12578c3e558c57fbfbe663be63259c1fce56f"  # constrained_batches, test-only

ISSUE1_TEXT = """\
one()/only() silently swallow a falsy custom exception

`one(iterable, too_long=exc)` (and the equivalent `too_short`, and `only`'s
`too_long`) are documented to raise the caller-supplied exception when the
iterable has too many/too few items. Internally this is implemented as:

    raise too_long or ValueError(msg)

If a caller passes an exception *instance* whose class overrides __bool__ to
return False (a legitimate, if unusual, pattern for "empty" sentinel
exceptions), `too_long or ValueError(msg)` evaluates the falsy `too_long` as
False and raises the generic ValueError instead of the caller's exception.

Separately, `msg` is built eagerly on every too-many-items path, which calls
repr() on the first two items even when a custom too_long is going to be
raised anyway -- if an item's __repr__ raises, that exception shadows the
real one.

Expected: the caller's too_long/too_short is always raised when given,
regardless of its truthiness, and the default message is only constructed
when no custom exception was supplied.
"""

ISSUE2_TEXT = """\
constrained_batches() accepts a nonpositive max_count without complaint

`constrained_batches(iterable, max_size, max_count=...)` validates that
*max_size* is positive:

    if max_size <= 0:
        raise ValueError('maximum size must be greater than zero')

but performs no equivalent check on *max_count*. Passing `max_count=0` or a
negative value silently produces batches with a bogus `batch_count == max_count`
comparison (0 == 0 is always true, so every batch is flushed after a single
item) instead of raising a clear error the way max_size already does.

Expected: max_count, like max_size, should raise ValueError('maximum count
must be greater than zero') for any max_count <= 0, right after the existing
max_size check.
"""


def prepare_sandbox(test_commit: str, commit_message: str) -> tuple[str, str]:
    """Clone the repo, check out SHARED_BASE_COMMIT, cherry-pick ONLY the
    regression-test half of a real fix commit (so the sandbox starts with
    failing tests and unfixed source), and return (repo_path, new_base_commit)."""
    sandbox = LocalSandbox(work_root=None)
    handle = sandbox.create(REPO_LOCAL_SOURCE, SHARED_BASE_COMMIT, run_id=uuid.uuid4().hex[:8])
    repo_path = handle.repo_path

    subprocess.run(["git", "cherry-pick", "-n", test_commit], cwd=repo_path, check=True)
    # cherry-pick -n staged the change in the index too, so `checkout -- <path>`
    # (which restores from the index) would be a no-op there; explicitly restore
    # any non-test files it touched from SHARED_BASE_COMMIT.
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True
    ).stdout.split()
    non_test = [f for f in changed if not f.startswith("tests/")]
    if non_test:
        subprocess.run(["git", "checkout", SHARED_BASE_COMMIT, "--", *non_test], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", commit_message], cwd=repo_path, check=True)
    new_base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True
    ).stdout.strip()

    return repo_path, new_base


# ---- issue #1: one()/only() -------------------------------------------

_ONE_ONLY_SHARED_BLOCK = (
    "    iterator = iter(iterable)\n"
    "    for first in iterator:\n"
    "        for second in iterator:\n"
    "            msg = (\n"
    "                f'Expected exactly one item in iterable, but got {first!r}, '\n"
    "                f'{second!r}, and perhaps more.'\n"
    "            )\n"
    "            raise too_long or ValueError(msg)\n"
    "        return first\n"
)
_ONE_ONLY_TRAILER = {
    "one": "    raise too_short or ValueError('too few items in iterable (expected 1)')\n",
    "only": "    return default\n",
}
_ONE_ONLY_FIXED_CORE = (
    "    iterator = iter(iterable)\n"
    "    for first in iterator:\n"
    "        for second in iterator:\n"
    "            if too_long is not None:\n"
    "                raise too_long\n"
    "            raise ValueError(\n"
    "                f'Expected exactly one item in iterable, but got {first!r}, '\n"
    "                f'{second!r}, and perhaps more.'\n"
    "            )\n"
    "        return first\n"
)


def _extract_one_only_block(repo_path: str, func_name: str) -> str:
    with open(os.path.join(repo_path, "more_itertools/more.py")) as f:
        content = f.read()
    def_idx = content.index(f"\ndef {func_name}(")
    block_idx = content.index(_ONE_ONLY_SHARED_BLOCK, def_idx)
    trailer = _ONE_ONLY_TRAILER[func_name]
    trailer_idx = content.index(trailer, block_idx)
    return content[block_idx : trailer_idx + len(trailer)]


def _one_only_fixed_blocks(one_old: str, only_old: str) -> tuple[str, str]:
    one_new = (
        _ONE_ONLY_FIXED_CORE
        + "    if too_short is not None:\n"
        "        raise too_short\n"
        "    raise ValueError('too few items in iterable (expected 1)')\n"
    )
    only_new = _ONE_ONLY_FIXED_CORE + _ONE_ONLY_TRAILER["only"]
    return one_new, only_new


def build_issue1_mock_adapter(repo_path: str) -> MockAdapter:
    """A cheap triage call sizes the budget, then a narrow, minimal-context
    localize/act pass -- narrow enough that it misses only()'s identical bug
    on the first attempt, catches it for real via the regression gate, and
    fixes it in a second, budget-aware pass. Ends with a memory-write call."""
    one_old = _extract_one_only_block(repo_path, "one")
    only_old = _extract_one_only_block(repo_path, "only")
    one_new, only_new = _one_only_fixed_blocks(one_old, only_old)

    script = [
        {"content": (
            '{"tier": "moderate", "justification": "Two isolated one-line control-flow bugs in '
            "well-tested pure functions, but the fix touches two similar call sites and needs "
            'regression coverage across both, so this is more than a trivial one-liner."}'
        )},
        {"content": (
            "The bug is in one() and only() in more_itertools/more.py: both use "
            "`raise too_long or ValueError(msg)`, which drops a falsy custom exception "
            "and eagerly builds `msg` (calling repr on items) even when too_long is set. "
            "A correct fix must check `is not None` explicitly and only build the default "
            "message on the no-custom-exception path."
        )},
        {"content": "Searching for the one() definition.", "tool": {"name": "search_symbol", "arguments": {"symbol_name": "one", "path": "more_itertools"}}},
        {"content": "Found one() (and by the same pattern, only()) in more_itertools/more.py at the `raise too_long or ValueError(msg)` line. No further search needed."},
        {"content": (
            "Plan: in one(), replace `msg = (...); raise too_long or ValueError(msg)` with an explicit "
            "`if too_long is not None: raise too_long` followed by `raise ValueError(...)` built lazily. "
            "I'll do only() the same way. I'll fix one() first and verify it before moving to only()."
        )},
        {"content": "Viewing one() before editing.", "tool": {"name": "open_file", "arguments": {"path": "more_itertools/more.py", "start_line": 656, "end_line": 666}}},
        {"content": "Applying the fix to one().", "tool": {"name": "edit_file", "arguments": {"path": "more_itertools/more.py", "old_str": one_old, "new_str": one_new}}},
        {"content": "Running the one() regression tests.", "tool": {"name": "run_tests", "arguments": {"test_path": "tests/test_more.py::OneTests::test_falsy_custom_exception tests/test_more.py::OneTests::test_too_long_does_not_evaluate_repr"}}},
        {"content": "one()'s tests pass. I believe the fix is complete."},
        {"content": (
            "The regression subset failed: OnlyTests::test_falsy_custom_exception still fails, because "
            "only() has the exact same `raise too_long or ValueError(msg)` bug and I only patched one(). "
            "I'll apply the identical fix to only() now."
        )},
        {"content": "Viewing only() before editing.", "tool": {"name": "open_file", "arguments": {"path": "more_itertools/more.py", "start_line": 3661, "end_line": 3672}}},
        {"content": "Applying the fix to only().", "tool": {"name": "edit_file", "arguments": {"path": "more_itertools/more.py", "old_str": only_old, "new_str": only_new}}},
        {"content": "Running the only() regression tests.", "tool": {"name": "run_tests", "arguments": {"test_path": "tests/test_more.py::OnlyTests"}}},
        {"content": "only()'s tests pass too. Both functions are fixed."},
        {"content": (
            '{"conventions": [{"note": "more_itertools/more.py is a single ~5600-line file holding every '
            "public function; tests live in tests/test_more.py in one TestCase per function, named "
            'FooTests for a function foo()."}], '
            '"landmines": [{"path": "more_itertools/more.py", "symbol": "one", "note": "one() and only() '
            "share the exact same `raise too_long or ValueError(msg)` block -- fixing one without the "
            'other passes locally but fails the regression gate on the other."}], '
            '"fix_patterns": [{"pattern": "falsy-value-swallowed-by-or-check", "example_diff_summary": '
            '"`raise X or default` silently drops a falsy X; replace with an explicit `if X is not None: '
            'raise X` / `else` split."}]}'
        )},
    ]
    return MockAdapter(script)


def build_naive_mock_adapter(repo_path: str) -> MockAdapter:
    """Phase 2 control condition: no triage call, no windowed/scoped reading,
    no incremental verification -- reaches the same correct patch, at far
    higher token cost."""
    one_old = _extract_one_only_block(repo_path, "one")
    only_old = _extract_one_only_block(repo_path, "only")
    one_new, only_new = _one_only_fixed_blocks(one_old, only_old)

    script = [
        {"content": (
            "I need to find why one() and only() don't raise falsy custom exceptions "
            "correctly. Let me look broadly across the codebase for every place this "
            "'too_long or ValueError' pattern might appear, since I don't yet know how "
            "many functions share this helper or whether it's duplicated inline."
        )},
        {"content": "Searching the whole repo for the pattern.", "tool": {"name": "search_code", "arguments": {"query": "too_long", "path": "."}}},
        {"content": "That's broad; let me just read the whole implementation file front-to-back to be safe.", "tool": {"name": "open_file", "arguments": {"path": "more_itertools/more.py", "start_line": 1, "end_line": 700}}},
        {"content": "one() is in there. only() is much further down -- let me read that region too, plus surrounding context in case it's structured differently.", "tool": {"name": "open_file", "arguments": {"path": "more_itertools/more.py", "start_line": 3550, "end_line": 3750}}},
        {"content": "Found both one() and only(); both use the same buggy pattern. No further search needed."},
        {"content": (
            "Plan: fix the `raise too_long or ValueError(msg)` pattern in both one() and only() "
            "by checking `is not None` explicitly, then run the full test suite to confirm nothing broke."
        )},
        {"content": "Applying the fix to one().", "tool": {"name": "edit_file", "arguments": {"path": "more_itertools/more.py", "old_str": one_old, "new_str": one_new}}},
        {"content": "Applying the fix to only().", "tool": {"name": "edit_file", "arguments": {"path": "more_itertools/more.py", "old_str": only_old, "new_str": only_new}}},
        {"content": "Running the full test suite to be thorough.", "tool": {"name": "run_tests", "arguments": {}}},
        {"content": "All tests pass across the full suite. Both functions are fixed."},
        {"content": "{}"},  # naive baseline: no memory write worth logging
    ]
    return MockAdapter(script)


# ---- issue #2: constrained_batches -------------------------------------

_CB_OLD_ANCHOR = (
    "    if max_size <= 0:\n"
    "        raise ValueError('maximum size must be greater than zero')\n"
    "\n"
    "    batch = []\n"
)
_CB_NEW_ANCHOR = (
    "    if max_size <= 0:\n"
    "        raise ValueError('maximum size must be greater than zero')\n"
    "    if max_count is not None and max_count <= 0:\n"
    "        raise ValueError('maximum count must be greater than zero')\n"
    "\n"
    "    batch = []\n"
)


def build_issue2_mock_adapter() -> MockAdapter:
    """Sutra's second run against the SAME repo: repo memory (populated by
    issue #1) supplies a fresh symbol-index cache for more_itertools/more.py,
    so Localize needs zero search tool calls -- it jumps straight to the
    cached line number. This is the concrete, measurable payoff Part D asks
    for, not a narrative embellishment."""
    script = [
        {"content": (
            '{"tier": "trivial", "justification": "A single missing validation check, '
            'mirroring an existing sibling check one line above it."}'
        )},
        {"content": (
            "constrained_batches() validates max_size but not max_count; a nonpositive "
            "max_count should raise ValueError the same way max_size already does."
        )},
        {"content": (
            "The repo-memory symbol index cache already has constrained_batches's line number in "
            "more_itertools/more.py from the one()/only() run (the cache was built from the whole "
            "file, not just the symbols touched then, and the file hasn't changed since). No fresh "
            "search needed -- jumping straight to that line."
        )},
        {"content": "Plan: add `if max_count is not None and max_count <= 0: raise ValueError(...)` right after the existing max_size check."},
        {"content": "Applying the fix.", "tool": {"name": "edit_file", "arguments": {"path": "more_itertools/more.py", "old_str": _CB_OLD_ANCHOR, "new_str": _CB_NEW_ANCHOR}}},
        {"content": "Running the constrained_batches regression tests.", "tool": {"name": "run_tests", "arguments": {"test_path": "tests/test_more.py::ConstrainedBatchesTests"}}},
        {"content": "Tests pass. The fix is complete."},
        {"content": (
            '{"conventions": [], '
            '"landmines": [], '
            '"fix_patterns": [{"pattern": "missing-sibling-validation", "example_diff_summary": '
            '"a validated parameter (max_size) has an unvalidated sibling (max_count) that should '
            'follow the same raise-ValueError pattern."}]}'
        )},
    ]
    return MockAdapter(script)


ISSUES = {
    "1": {
        "test_commit": ISSUE1_FIX_COMMIT,
        "commit_message": "test: add regression tests for one()/only() falsy-exception bug",
        "issue_text": ISSUE1_TEXT,
        "issue_id": "one-only-falsy-exception",
        "target_test": "tests/test_more.py::OneTests::test_falsy_custom_exception",
        "regression_test_paths": ["tests/test_more.py::OneTests", "tests/test_more.py::OnlyTests"],
        "mock_builder": lambda repo_path: build_issue1_mock_adapter(repo_path),
        "naive_mock_builder": lambda repo_path: build_naive_mock_adapter(repo_path),
    },
    "2": {
        "test_commit": ISSUE2_TEST_COMMIT,
        "commit_message": "test: add regression tests for constrained_batches nonpositive max_count",
        "issue_text": ISSUE2_TEXT,
        "issue_id": "constrained-batches-nonpositive-max-count",
        "target_test": "tests/test_more.py::ConstrainedBatchesTests::test_nonpositive_max_count",
        "regression_test_paths": ["tests/test_more.py::ConstrainedBatchesTests"],
        "mock_builder": lambda repo_path: build_issue2_mock_adapter(),
        "naive_mock_builder": None,
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 0-3 demo driver")
    parser.add_argument("--issue", choices=["1", "2"], default="1", help="which real issue to solve (default: 1)")
    parser.add_argument(
        "--naive-baseline",
        action="store_true",
        help="control condition (issue 1 only): skip triage, always use the largest budget profile, "
        "never force an early phase transition.",
    )
    parser.add_argument(
        "--reset-memory",
        action="store_true",
        help="wipe this repo's memory bank before running -- use before issue #1 for a genuinely "
        "fresh/empty memory bank in the two-issue demo.",
    )
    args = parser.parse_args()

    issue = ISSUES[args.issue]
    if args.naive_baseline and issue["naive_mock_builder"] is None:
        parser.error(f"--naive-baseline has no control script for issue {args.issue}")

    print(f"[demo] preparing sandbox for issue #{args.issue} (shared base commit, cherry-picked regression tests)...")
    repo_path, base_commit = prepare_sandbox(issue["test_commit"], issue["commit_message"])
    print(f"[demo] sandbox ready at {repo_path}, base_commit={base_commit[:10]}")

    if args.reset_memory:
        repo_id = repo_id_for(repo_path)
        memory_path = os.path.join(STORE_DIR, f"{repo_id}.json")
        if os.path.exists(memory_path):
            os.remove(memory_path)
            print(f"[demo] wiped repo memory at {memory_path}")

    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        from harness.model_adapter.litellm_adapter import LiteLLMAdapter

        print("[demo] GROQ_API_KEY found -- using live LiteLLMAdapter(groq/llama-3.3-70b-versatile)")
        adapter = LiteLLMAdapter(model="groq/llama-3.3-70b-versatile", api_key_env="GROQ_API_KEY")
    elif args.naive_baseline:
        print("[demo] no GROQ_API_KEY set -- using scripted MockAdapter (naive-baseline control script)")
        adapter = issue["naive_mock_builder"](repo_path)
    else:
        print("[demo] no GROQ_API_KEY set -- using scripted MockAdapter (deterministic replay)")
        adapter = issue["mock_builder"](repo_path)

    demo_dir = os.path.dirname(os.path.abspath(__file__))
    trajectory_path = os.path.join(demo_dir, "trajectory.jsonl")
    if os.path.exists(trajectory_path):
        os.remove(trajectory_path)
    trajectory = TrajectoryStore(trajectory_path)

    config = RunConfig(
        repo_path=repo_path,
        base_commit=base_commit,
        issue_text=issue["issue_text"],
        target_test=issue["target_test"],
        regression_test_paths=issue["regression_test_paths"],
        issue_id=issue["issue_id"],
    )

    orchestrator = Orchestrator(adapter, config, trajectory, naive_baseline=args.naive_baseline)
    result = orchestrator.run()

    print()
    print("=" * 70)
    print(f"VERIFIED: {result.verified}   tier={result.tier}")
    print(f"triage justification: {result.triage_justification}")
    for name, g in result.gates.items():
        status = "PASS" if g["passed"] else "FAIL"
        print(f"  [{status}] {name}: {g['detail']}")
    print(f"retries_used={result.retries_used}  total_tokens={result.total_tokens}  total_tool_calls={result.total_tool_calls}")
    print("=" * 70)
    print()
    print("--- final diff ---")
    print(result.diff)
    print(f"[demo] trajectory log written to {trajectory_path}")
    print(f"[demo] sandbox repo left at {repo_path} for inspection")

    return 0 if result.verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
