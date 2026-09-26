"""Phase 0 end-to-end demo driver.

Solves a real GitHub issue-equivalent bug in more-itertools (the one()/only()
`too_long or ValueError(...)` bug, fixed for real in upstream commit def2dab)
on a fresh sandboxed clone, verified by the repo's own pytest suite.

No live model calls are required to run this (MockAdapter replays a scripted
trajectory), but the orchestrator, tools, sandbox, budget tracker, verifier
gates and trajectory log are all exercised for real -- nothing here is faked
except "what the LLM would have said". Swap `build_adapter()` to return a
LiteLLMAdapter once GROQ_API_KEY (or another provider's key) is set, and the
exact same run() call drives a live agent instead.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.memory.trajectory_store import TrajectoryStore
from harness.model_adapter.mock_adapter import MockAdapter
from harness.orchestrator.state_machine import Orchestrator, RunConfig
from harness.sandbox.local_sandbox import LocalSandbox

REPO_LOCAL_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scratch_repos", "more-itertools")
BASE_COMMIT = "9ed3dbb0ae527230cd156d91d0af305478558fba"
FIX_COMMIT = "def2dabea858b6ecb84ee0c52e6e07929f2c409c"

ISSUE_TEXT = """\
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


def prepare_sandbox() -> tuple[str, str]:
    """Clone the repo, check out the pre-fix commit, cherry-pick ONLY the
    regression-test half of the real fix commit (so the sandbox starts with
    failing tests and unfixed source -- exactly what the agent must resolve),
    and return (repo_path, new_base_commit)."""
    sandbox = LocalSandbox(work_root=None)
    handle = sandbox.create(REPO_LOCAL_SOURCE, BASE_COMMIT, run_id=uuid.uuid4().hex[:8])
    repo_path = handle.repo_path

    subprocess.run(["git", "cherry-pick", "-n", FIX_COMMIT], cwd=repo_path, check=True)
    # cherry-pick -n staged the fix in the index too, so `checkout -- <path>` (which
    # restores from the index) would be a no-op; restore from BASE_COMMIT explicitly.
    subprocess.run(["git", "checkout", BASE_COMMIT, "--", "more_itertools/more.py", "docs/versions.rst"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "test: add regression tests for one()/only() falsy-exception bug"], cwd=repo_path, check=True)
    new_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True).stdout.strip()

    return repo_path, new_base


_SHARED_BLOCK = (
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
_TRAILER_BY_FUNC = {
    "one": "    raise too_short or ValueError('too few items in iterable (expected 1)')\n",
    "only": "    return default\n",
}


def _extract_block(repo_path: str, func_name: str) -> str:
    """Pull the exact unfixed block out of the live sandbox file, anchored on the
    specific `def one(`/`def only(` occurrence, so the scripted edit's old_str is
    guaranteed to match byte-for-byte AND be unique in the file."""
    with open(os.path.join(repo_path, "more_itertools/more.py")) as f:
        content = f.read()
    def_idx = content.index(f"\ndef {func_name}(")
    block_idx = content.index(_SHARED_BLOCK, def_idx)
    trailer = _TRAILER_BY_FUNC[func_name]
    trailer_idx = content.index(trailer, block_idx)
    end = trailer_idx + len(trailer)
    return content[block_idx:end]


_FIXED_CORE = (
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


def _fixed_blocks(one_old: str, only_old: str) -> tuple[str, str]:
    one_new = (
        _FIXED_CORE
        + "    if too_short is not None:\n"
        "        raise too_short\n"
        "    raise ValueError('too few items in iterable (expected 1)')\n"
    )
    only_new = _FIXED_CORE + _TRAILER_BY_FUNC["only"]
    return one_new, only_new


def build_mock_adapter_with_blocks(one_old: str, only_old: str) -> MockAdapter:
    """Sutra's scripted trajectory: a cheap triage call sizes the budget, then a
    narrow, minimal-context localize/act pass -- narrow enough that it misses
    only()'s identical bug on the first attempt, catches it for real via the
    regression gate, and fixes it in a second, budget-aware pass."""
    one_new, only_new = _fixed_blocks(one_old, only_old)

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
    ]
    return MockAdapter(script)


def build_naive_mock_adapter_with_blocks(one_old: str, only_old: str) -> MockAdapter:
    """Control condition: no triage call (skipped by naive_baseline mode itself),
    no windowed/scoped reading, no incremental verification -- it dumps large
    unscoped chunks of the file into context (which then sit in every later
    turn's prompt, uncompressed) and reruns the *entire* suite on every check.
    It reaches the same correct patch, just by doing substantially more,
    heavier-context work along the way."""
    one_new, only_new = _fixed_blocks(one_old, only_old)

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
    ]
    return MockAdapter(script)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 0-2 demo driver")
    parser.add_argument(
        "--naive-baseline",
        action="store_true",
        help="control condition: skip triage, always use the largest ('complex') budget profile, "
        "never force an early phase transition -- everything else (same issue, same sandbox, same "
        "verifier) is identical to the normal run.",
    )
    args = parser.parse_args()

    print("[demo] preparing sandbox (clone + checkout base commit + cherry-pick regression tests)...")
    repo_path, base_commit = prepare_sandbox()
    print(f"[demo] sandbox ready at {repo_path}, base_commit={base_commit[:10]}")

    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        from harness.model_adapter.litellm_adapter import LiteLLMAdapter

        print("[demo] GROQ_API_KEY found -- using live LiteLLMAdapter(groq/llama-3.3-70b-versatile)")
        adapter = LiteLLMAdapter(model="groq/llama-3.3-70b-versatile", api_key_env="GROQ_API_KEY")
    else:
        one_old = _extract_block(repo_path, "one")
        only_old = _extract_block(repo_path, "only")
        if args.naive_baseline:
            print("[demo] no GROQ_API_KEY set -- using scripted MockAdapter (naive-baseline control script)")
            adapter = build_naive_mock_adapter_with_blocks(one_old, only_old)
        else:
            print("[demo] no GROQ_API_KEY set -- using scripted MockAdapter (deterministic replay)")
            adapter = build_mock_adapter_with_blocks(one_old, only_old)

    demo_dir = os.path.dirname(os.path.abspath(__file__))
    trajectory_path = os.path.join(demo_dir, "trajectory.jsonl")
    if os.path.exists(trajectory_path):
        os.remove(trajectory_path)
    trajectory = TrajectoryStore(trajectory_path)

    config = RunConfig(
        repo_path=repo_path,
        base_commit=base_commit,
        issue_text=ISSUE_TEXT,
        target_test="tests/test_more.py::OneTests::test_falsy_custom_exception",
        regression_test_paths=["tests/test_more.py::OneTests", "tests/test_more.py::OnlyTests"],
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
