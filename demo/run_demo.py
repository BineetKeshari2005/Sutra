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
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.memory.repo_memory import STORE_DIR, repo_id_for
from harness.memory.trajectory_store import TrajectoryStore
from harness.model_adapter.litellm_adapter import _load_dotenv_once
from harness.model_adapter.mock_adapter import MockAdapter
from harness.orchestrator.state_machine import MAX_REFLECT_RETRIES, Orchestrator, RunConfig
from harness.sandbox.local_sandbox import LocalSandbox

_load_dotenv_once()

REPO_LOCAL_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scratch_repos", "more-itertools")

# Ancestor of BOTH real fixes below, so more_itertools/more.py is byte-identical
# going into either issue's sandbox -- required for the symbol-index cache's
# content fingerprint to genuinely match across issue #1 and issue #2.
SHARED_BASE_COMMIT = "19ddb972845ab0e5b9b7449d3fd5930781407441"

ISSUE1_FIX_COMMIT = "def2dabea858b6ecb84ee0c52e6e07929f2c409c"  # one()/only(), test+fix combined
ISSUE2_TEST_COMMIT = "87d12578c3e558c57fbfbe663be63259c1fce56f"  # constrained_batches, test-only

# Issue #3 uses its own base commit (doesn't need to share a fingerprint with
# anything -- it's demonstrating Part A's abstention path, not repo memory).
ISSUE3_BASE_COMMIT = "516f0a80fb7c2c8562dbb5e318fc2a3d44f4171f"
ISSUE3_FIX_COMMIT = "0e6acdf9b60765ecf9634d6f5c132ac1bebc616b"  # chunked(), test+fix combined

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

ISSUE3_TEXT = """\
chunked() leaks an internal islice error message for negative n

`chunked(iterable, n)` is the only one of the sizing functions (sliced(),
tail(), etc.) without a guard on a negative *n*: passing n=-1 leaks islice's
internal message ("Stop argument for islice() must be None or an integer:
0 <= x <= sys.maxsize") instead of a clear, repo-consistent error.

Expected: chunked() should validate n up front and raise
`ValueError('n must be at least 0')` for any n < 0, exactly matching the
wording sliced() and tail() already use for the same condition. n=None
(single chunk) and n=0 must remain unchanged.
"""


def prepare_sandbox(base_commit: str, test_commit: str, commit_message: str) -> tuple[str, str]:
    """Clone the repo, check out base_commit, cherry-pick ONLY the
    regression-test half of a real fix commit (so the sandbox starts with
    failing tests and unfixed source), and return (repo_path, new_base_commit)."""
    sandbox = LocalSandbox(work_root=None)
    handle = sandbox.create(REPO_LOCAL_SOURCE, base_commit, run_id=uuid.uuid4().hex[:8])
    repo_path = handle.repo_path

    subprocess.run(["git", "cherry-pick", "-n", test_commit], cwd=repo_path, check=True)
    # cherry-pick -n staged the change in the index too, so `checkout -- <path>`
    # (which restores from the index) would be a no-op there; explicitly restore
    # any non-test files it touched from base_commit.
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True
    ).stdout.split()
    non_test = [f for f in changed if not f.startswith("tests/")]
    if non_test:
        subprocess.run(["git", "checkout", base_commit, "--", *non_test], cwd=repo_path, check=True)
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
        {"content": (
            '{"verdict": "no_concerns", "notes": "The diff replaces the `or`-based check with explicit '
            '`is not None` checks in both one() and only(), directly matching the issue -- no test-gaming '
            'patterns (no hardcoded values, no broadened exception handling)."}'
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
        {"content": (
            '{"verdict": "no_concerns", "notes": "Same fix as the budgeted run -- explicit `is not None` '
            'checks in one() and only() -- just reached via a more expensive path."}'
        )},
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
        {"content": (
            '{"verdict": "no_concerns", "notes": "Adds a validation check mirroring the existing '
            'max_size check one line above -- minimal, matches the issue, no test-gaming."}'
        )},
    ]
    return MockAdapter(script)


# ---- issue #3: chunked() -- deliberately exercised with --max-retries 0 ----
# so a genuinely incomplete first attempt (real gate failure, not fabricated)
# demonstrates Part A's calibrated-abstention path instead of retrying past it.

_CHUNKED_OLD_ANCHOR = "    iterator = iter(partial(take, n, iter(iterable)), [])\n    if strict:\n"
# Plausible but WRONG: the agent picks its own wording instead of matching the
# sibling convention (sliced()/tail() both say "n must be at least 0"), so the
# real regex-matching test genuinely fails when this actually runs.
_CHUNKED_NEW_ANCHOR_WRONG = (
    "    if n is not None and n < 0:\n"
    "        raise ValueError('n cannot be negative')\n"
    "\n"
    "    iterator = iter(partial(take, n, iter(iterable)), [])\n"
    "    if strict:\n"
)


def build_issue3_mock_adapter() -> MockAdapter:
    """A plausible-but-wrong single attempt: validates n, but with a message
    that doesn't match the sibling convention the test asserts against. Run
    with --max-retries 0, this is a REAL pytest failure (not scripted to fail)
    that Part A's abstention path has to handle honestly."""
    script = [
        {"content": (
            '{"tier": "trivial", "justification": "A single missing validation check on one function."}'
        )},
        {"content": "chunked() should raise ValueError for negative n, the way sliced() and tail() already do."},
        {"content": "Searching for chunked().", "tool": {"name": "search_symbol", "arguments": {"symbol_name": "chunked", "path": "more_itertools"}}},
        {"content": "Found chunked() -- no further search needed."},
        {"content": "Plan: add a guard at the top of chunked() raising ValueError for n < 0."},
        {"content": "Applying the fix.", "tool": {"name": "edit_file", "arguments": {"path": "more_itertools/more.py", "old_str": _CHUNKED_OLD_ANCHOR, "new_str": _CHUNKED_NEW_ANCHOR_WRONG}}},
        {"content": "Running the regression test.", "tool": {"name": "run_tests", "arguments": {"test_path": "tests/test_more.py::ChunkedTests::test_negative"}}},
        {"content": "The test still fails -- the exception is raised, but the message doesn't match what the test expects."},
        {"content": (
            '{"conventions": [], '
            '"landmines": [{"path": "more_itertools/more.py", "symbol": "chunked", "note": "sizing-function '
            'error messages (sliced(), tail()) use the exact wording \\"n must be at least 0\\" -- match it '
            'verbatim rather than paraphrasing."}], '
            '"fix_patterns": []}'
        )},
        {"content": (
            '{"root_cause_confidence": "medium", '
            '"root_cause_summary": "chunked() now validates negative n, but the error message wording '
            '(\\"n cannot be negative\\") does not match the exact string the regression test asserts '
            '(\\"n must be at least 0\\"), which mirrors sliced()/tail()\'s existing convention.", '
            '"unresolved_issue": "The fix is functionally close but the retry budget was exhausted before '
            'a second attempt could correct the message wording to match the sibling functions."}'
        )},
    ]
    return MockAdapter(script)


ISSUES = {
    "1": {
        "base_commit": SHARED_BASE_COMMIT,
        "test_commit": ISSUE1_FIX_COMMIT,
        "commit_message": "test: add regression tests for one()/only() falsy-exception bug",
        "issue_text": ISSUE1_TEXT,
        "issue_id": "one-only-falsy-exception",
        "target_test": "tests/test_more.py::OneTests::test_falsy_custom_exception",
        "regression_test_paths": ["tests/test_more.py::OneTests", "tests/test_more.py::OnlyTests"],
        "mock_builder": lambda repo_path: build_issue1_mock_adapter(repo_path),
        "naive_mock_builder": lambda repo_path: build_naive_mock_adapter(repo_path),
        "default_max_retries": None,
    },
    "2": {
        "base_commit": SHARED_BASE_COMMIT,
        "test_commit": ISSUE2_TEST_COMMIT,
        "commit_message": "test: add regression tests for constrained_batches nonpositive max_count",
        "issue_text": ISSUE2_TEXT,
        "issue_id": "constrained-batches-nonpositive-max-count",
        "target_test": "tests/test_more.py::ConstrainedBatchesTests::test_nonpositive_max_count",
        "regression_test_paths": ["tests/test_more.py::ConstrainedBatchesTests"],
        "mock_builder": lambda repo_path: build_issue2_mock_adapter(),
        "naive_mock_builder": None,
        "default_max_retries": None,
    },
    "3": {
        "base_commit": ISSUE3_BASE_COMMIT,
        "test_commit": ISSUE3_FIX_COMMIT,
        "commit_message": "test: add regression test for chunked() negative n",
        "issue_text": ISSUE3_TEXT,
        "issue_id": "chunked-negative-n",
        "target_test": "tests/test_more.py::ChunkedTests::test_negative",
        "regression_test_paths": ["tests/test_more.py::ChunkedTests"],
        "mock_builder": lambda repo_path: build_issue3_mock_adapter(),
        "naive_mock_builder": None,
        "default_max_retries": 0,  # exercise the abstention path on a real, unforced gate failure
    },
}


# Checked in this order -- first match wins. Gemini/DeepSeek/Qwen/OpenAI/Groq
# model strings verified directly against LiteLLM's own provider docs, not
# guessed: https://docs.litellm.ai/docs/providers/deepseek and
# https://docs.litellm.ai/docs/providers/dashscope (Qwen). LiteLLMAdapter now
# passes the key explicitly (harness/model_adapter/litellm_adapter.py), so
# each of these can use whatever env var name it lists here regardless of
# what litellm's own per-provider convention would otherwise expect (e.g.
# DashScope normally wants DASHSCOPE_API_KEY, not QWEN_API_KEY).
_LIVE_ADAPTER_CANDIDATES = [
    ("GEMINI_API_KEY", "gemini/gemini-3.1-flash-lite", "Gemini"),
    ("DEEPSEEK_API_KEY", "deepseek/deepseek-chat", "DeepSeek"),
    ("QWEN_API_KEY", "dashscope/qwen-turbo", "Qwen"),
    ("OPENAI_API_KEY", "gpt-4o-mini", "OpenAI"),
    ("GROQ_API_KEY", "groq/qwen/qwen3.8-27b", "Groq"),
]


def _detect_live_adapter() -> Any:
    """Returns a configured LiteLLMAdapter for the first supported API key
    found in the environment, or None if none are set (callers decide what
    "none found" means for them -- MockAdapter fallback in fixture mode,
    a hard error in live/interactive mode, since those have no fixture
    script to fall back to). Always prints exactly one line naming what
    it picked."""
    from harness.model_adapter.litellm_adapter import LiteLLMAdapter

    for env_var, model, label in _LIVE_ADAPTER_CANDIDATES:
        if os.environ.get(env_var):
            print(f"Using {label} ({model})")
            return LiteLLMAdapter(model=model, api_key_env=env_var)

    print("No API key found — running in MockAdapter (offline demo) mode")
    return None


def live_repo_run(
    repo_url: str, issue_file: str, max_retries: int, reset_memory: bool,
    naive_baseline: bool = False, create_pr: bool = False,
    allow_pr_target: str | None = None, confirm_pr: bool = False,
) -> int:
    """Run the orchestrator against an arbitrary GitHub repo + plain-text issue file.
    Auto-detects the first available API key (see _LIVE_ADAPTER_CANDIDATES).
    No cherry-pick: clones HEAD directly.
    Test gates are skipped (target_test=None) since we don't know the test runner.
    """
    has_any_key = any(os.environ.get(env_var) for env_var, _, _ in _LIVE_ADAPTER_CANDIDATES)
    if not has_any_key:
        print(
            "[live] ERROR: set one of "
            + ", ".join(env_var for env_var, _, _ in _LIVE_ADAPTER_CANDIDATES)
            + " in .env"
        )
        return 1

    github_token = os.environ.get("GITHUB_TOKEN")
    if create_pr and not github_token:
        print(
            "[live] ERROR: --create-pr requires GITHUB_TOKEN to be set (env var or .env file).\n"
            "        Create a token at https://github.com/settings/tokens with 'repo' scope, "
            "then add GITHUB_TOKEN=ghp_... to .env"
        )
        return 1
    if create_pr:
        # Fail loud and early -- before cloning or spending a single token on
        # the agent loop -- rather than only discovering a misconfigured
        # --create-pr after the whole run finishes. create_pull_request()
        # re-checks this itself regardless, so this is a fast-fail convenience,
        # not the actual enforcement point.
        from harness.integrations.github_pr import check_pr_authorization

        authorized, reason = check_pr_authorization(repo_url, allow_pr_target, confirm_pr)
        if not authorized:
            print(f"[live] ERROR: --create-pr refused: {reason}")
            print("        Pass --allow-pr-target <repo-url> (matching --repo exactly) and --confirm-pr.")
            return 1

    with open(issue_file, encoding="utf-8") as f:
        issue_text = f.read().strip()
    if not issue_text:
        print(f"[live] ERROR: issue file '{issue_file}' is empty.")
        return 1

    # Derive a short issue-id from the filename
    issue_id = os.path.splitext(os.path.basename(issue_file))[0]

    print(f"[live] cloning {repo_url} ...")
    sandbox = LocalSandbox(work_root=None)
    import tempfile
    run_id = uuid.uuid4().hex[:8]
    repo_path = tempfile.mkdtemp(prefix=f"agent-run-{run_id}-")

    # Clone and get current HEAD -- no cherry-pick, no base-commit surgery
    import shutil
    try:
        subprocess.run(["git", "clone", "--quiet", "--depth=50", repo_url, repo_path], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[live] ERROR: git clone failed: {e}")
        shutil.rmtree(repo_path, ignore_errors=True)
        return 1

    subprocess.run(["git", "config", "user.email", "agent@harness.local"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "SWE Agent Harness"], cwd=repo_path, check=True)

    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    print(f"[live] cloned OK. HEAD = {base_commit[:12]}  repo at {repo_path}")

    if reset_memory:
        repo_id = repo_id_for(repo_path)
        memory_path = os.path.join(STORE_DIR, f"{repo_id}.json")
        if os.path.exists(memory_path):
            os.remove(memory_path)
            print(f"[live] wiped repo memory at {memory_path}")

    adapter = _detect_live_adapter()
    # has_any_key was already checked above, so this should never be None here.

    demo_dir = os.path.dirname(os.path.abspath(__file__))
    trajectory_path = os.path.join(demo_dir, "trajectory.jsonl")
    if os.path.exists(trajectory_path):
        os.remove(trajectory_path)
    trajectory = TrajectoryStore(trajectory_path)

    config = RunConfig(
        repo_path=repo_path,
        base_commit=base_commit,
        issue_text=issue_text,
        target_test=None,          # No pytest harness for non-Python repos
        regression_test_paths=None,
        issue_id=issue_id,
    )

    if naive_baseline:
        print("[live] --naive-baseline: skipping triage, using the largest ('complex') budget profile, "
              "never forcing an early phase transition")

    orchestrator = Orchestrator(
        adapter, config, trajectory,
        naive_baseline=naive_baseline,
        max_retries=max_retries,
    )
    result = orchestrator.run()

    print()
    print("=" * 70)
    print(f"STATUS: {result.status}   tier={result.tier}")
    print(f"triage justification: {result.triage_justification}")
    for name, g in result.gates.items():
        status = "PASS" if g["passed"] else "FAIL"
        print(f"  [{status}] {name}: {g['detail']}")
    print(f"retries_used={result.retries_used}  total_tokens={result.total_tokens}  total_tool_calls={result.total_tool_calls}")
    if result.status == "unresolved":
        print(f"best_checkpoint={result.best_checkpoint}")
        print(f"confidence_report={result.confidence_report}")
    if result.adversarial_review:
        print(f"adversarial_review={result.adversarial_review}")
    print("=" * 70)
    print()
    print("--- final diff (review this before applying!) ---")
    print(result.diff)
    print(f"[live] trajectory log written to {trajectory_path}")
    print(f"[live] sandbox repo left at {repo_path} for inspection")
    print(f"[live] NOTE: test gates were skipped (no pytest harness). Review the diff manually.")

    if create_pr:
        if not result.verified:
            print(f"[live] --create-pr requested but status is '{result.status}', not 'verified' -- skipping PR creation.")
        else:
            from harness.integrations.github_pr import GitHubPRError, create_pull_request

            print("[live] opening a pull request...")
            try:
                pr_url = create_pull_request(
                    repo_path=repo_path,
                    repo_url=repo_url,
                    issue_text=issue_text,
                    issue_id=issue_id,
                    result=result,
                    github_token=github_token,
                    allowed_target=allow_pr_target,
                    confirmed=confirm_pr,
                    trajectory=trajectory,
                )
                print(f"[live] PR opened: {pr_url}")
            except GitHubPRError as e:
                print(f"[live] ERROR: PR creation failed: {e}")
                return 1

    return 0 if result.verified else 1


def _parse_owner_repo_from_url(repo_url: str) -> tuple[str, str] | None:
    from urllib.parse import urlparse

    parsed = urlparse(repo_url if "://" in repo_url else f"https://{repo_url}")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    return parts[0], parts[1].removesuffix(".git")


def _parse_github_issue_ref(raw: str, repo_url: str) -> tuple[str, str, int] | None:
    """A full GitHub issue URL, or a bare issue number/`#number` resolved
    against `repo_url` (the repo already given earlier in setup). Returns
    None if `raw` doesn't look like either -- callers fall through to
    treating it as raw text."""
    import re

    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)/?$", raw.strip())
    if m:
        return m.group(1), m.group(2).removesuffix(".git"), int(m.group(3))

    m = re.match(r"^#?(\d+)$", raw.strip())
    if m and repo_url:
        owner_repo = _parse_owner_repo_from_url(repo_url)
        if owner_repo:
            return owner_repo[0], owner_repo[1], int(m.group(1))

    return None


def _fetch_github_issue_text(owner: str, repo: str, number: int) -> str:
    """Fetches an issue's real title + body from the GitHub API -- public
    issues need no token, but GITHUB_TOKEN (if set) is used when present for
    a higher rate limit and for private repos."""
    import json
    import urllib.error
    import urllib.request

    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"could not fetch issue #{number} from {owner}/{repo}: {e.code} {e.reason}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach GitHub to fetch issue #{number}: {e.reason}") from None

    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    return f"{title}\n\n{body}".strip()


def _detect_issue_text(raw: str, repo_url: str = "") -> str:
    """A file path if `raw` names an existing file; a real fetched issue if
    `raw` is a GitHub issue URL or a bare issue number (resolved against
    `repo_url`); otherwise the raw text itself -- no separate prompt asking
    which, it's auto-detected."""
    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as f:
            return f.read().strip()

    issue_ref = _parse_github_issue_ref(raw, repo_url)
    if issue_ref:
        owner, repo, number = issue_ref
        print(f"Fetching issue #{number} from {owner}/{repo} ...")
        return _fetch_github_issue_text(owner, repo, number)

    return raw.strip()


def _prompt_repo() -> str:
    while True:
        repo = input("Which repository? (URL or local path): ").strip()
        if repo:
            return repo
        print("  Please enter a repository URL or path.")


def _prompt_issue(repo_url: str) -> str:
    while True:
        raw = input("What's the issue? (paste text, a GitHub issue URL/number, or a path to a file): ").strip()
        if not raw:
            print("  Please enter issue text, a file path, or a GitHub issue URL/number.")
            continue
        try:
            return _detect_issue_text(raw, repo_url)
        except RuntimeError as e:
            print(f"  {e}")
            print("  Try again, or paste the issue text directly instead.")


def _confirm_start(repo_url: str, issue_text: str) -> bool:
    preview = issue_text[:150].replace("\n", " ")
    if len(issue_text) > 150:
        preview += "..."
    print()
    print(f"Repo: {repo_url}")
    print(f"Issue preview: {preview}")
    try:
        input("Press Enter to start, or Ctrl+C to cancel: ")
        return True
    except KeyboardInterrupt:
        print("\nCancelled.")
        return False


def _clone_and_run_interactive(
    repo_url: str, issue_text: str, issue_id: str,
) -> tuple[Any, str, TrajectoryStore] | tuple[None, None, None]:
    """Clone + run for the interactive flow specifically -- deliberately a
    separate function from live_repo_run rather than a shared refactor, so
    the existing flag-based path's behavior stays byte-for-byte unchanged
    (Part A's explicit requirement) no matter what this one needs to return
    or how its control flow evolves.
    """
    has_any_key = any(os.environ.get(env_var) for env_var, _, _ in _LIVE_ADAPTER_CANDIDATES)
    if not has_any_key:
        print("ERROR: set one of " + ", ".join(env_var for env_var, _, _ in _LIVE_ADAPTER_CANDIDATES) + " in .env")
        return None, None, None

    print(f"Cloning {repo_url} ...")
    import shutil
    import tempfile

    run_id = uuid.uuid4().hex[:8]
    repo_path = tempfile.mkdtemp(prefix=f"agent-run-{run_id}-")
    try:
        subprocess.run(["git", "clone", "--quiet", "--depth=50", repo_url, repo_path], check=True)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: git clone failed: {e}")
        shutil.rmtree(repo_path, ignore_errors=True)
        return None, None, None

    subprocess.run(["git", "config", "user.email", "agent@harness.local"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "SWE Agent Harness"], cwd=repo_path, check=True)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    print(f"Cloned OK. HEAD = {base_commit[:12]}  repo at {repo_path}")

    adapter = _detect_live_adapter()

    demo_dir = os.path.dirname(os.path.abspath(__file__))
    trajectory_path = os.path.join(demo_dir, "trajectory.jsonl")
    if os.path.exists(trajectory_path):
        os.remove(trajectory_path)
    trajectory = TrajectoryStore(trajectory_path)

    config = RunConfig(
        repo_path=repo_path,
        base_commit=base_commit,
        issue_text=issue_text,
        target_test=None,
        regression_test_paths=None,
        issue_id=issue_id,
    )

    print()
    print("Running -- Understand -> Localize -> Plan -> Act -> Verify -> Finalize, uninterrupted...")
    print()
    orchestrator = Orchestrator(adapter, config, trajectory, max_retries=MAX_REFLECT_RETRIES)
    result = orchestrator.run()
    return result, repo_path, trajectory


def _print_run_summary(result: Any) -> None:
    print()
    print("=" * 70)
    print(f"Run complete: {result.status.upper()}")
    print(f"triage justification: {result.triage_justification}")
    for name, g in result.gates.items():
        status = "PASS" if g["passed"] else "FAIL"
        print(f"  [{status}] {name}: {g['detail']}")
    print(f"retries_used={result.retries_used}  total_tokens={result.total_tokens}  total_tool_calls={result.total_tool_calls}")
    if result.adversarial_review:
        print(f"adversarial_review={result.adversarial_review}")
    print("=" * 70)


def _show_diff_and_confidence(result: Any) -> None:
    print()
    print("--- diff ---")
    print(result.diff or "(no diff)")
    if result.status == "unresolved":
        cr = result.confidence_report or {}
        print()
        print("--- confidence report ---")
        print(f"best_checkpoint: {result.best_checkpoint}")
        print(f"gates at best checkpoint: {result.gates}")
        print(f"root_cause_confidence: {cr.get('root_cause_confidence')}")
        print(f"root_cause_summary: {cr.get('root_cause_summary')}")
        print(f"unresolved_issue: {cr.get('unresolved_issue')}")


def _create_pr_flow_interactive(repo_path: str, repo_url: str, issue_text: str, issue_id: str, result: Any, trajectory: TrajectoryStore) -> bool:
    """Returns True once a PR is actually opened, so the caller can end the
    session cleanly on success instead of looping back to the full menu --
    False on cancellation/failure, so the caller keeps offering the menu to
    retry or end."""
    if not result.verified:
        print(f"Cannot create a PR: run status is '{result.status}', not verified.")
        return False

    import getpass

    print("(input is hidden -- nothing will appear as you type, that's expected; paste/type then press Enter)")
    token = getpass.getpass("GitHub personal access token (repo scope): ").strip()
    if not token:
        print("No token entered -- cancelling PR creation.")
        return False
    print(f"Token received ({len(token)} characters). Not shown, for your safety.")

    from harness.integrations.github_pr import GitHubPRError, create_pull_request

    print("Opening a pull request...")
    try:
        pr_url = create_pull_request(
            repo_path=repo_path,
            repo_url=repo_url,
            issue_text=issue_text,
            issue_id=issue_id,
            result=result,
            github_token=token,
            interactive=True,
            trajectory=trajectory,
        )
        print()
        print("=" * 70)
        print("PR opened successfully!")
        print(pr_url)
        print("=" * 70)
        return True
    except GitHubPRError as e:
        print(f"ERROR: PR creation failed: {e}")
        return False


def _review_submenu() -> str:
    while True:
        print()
        print("  1) Create PR")
        print("  2) End")
        choice = input("  Choice: ").strip()
        if choice == "1":
            return "pr"
        if choice == "2":
            return "end"
        print("  Please choose 1 or 2.")


def _post_run_menu(repo_path: str, repo_url: str, issue_text: str, issue_id: str, result: Any, trajectory: TrajectoryStore) -> int:
    while True:
        print()
        print("What would you like to do?")
        print("  1) Review code")
        print("  2) Create PR")
        print("  3) End")
        choice = input("Choice: ").strip()

        if choice == "1":
            _show_diff_and_confidence(result)
            if _review_submenu() == "pr":
                if _create_pr_flow_interactive(repo_path, repo_url, issue_text, issue_id, result, trajectory):
                    print("Goodbye.")
                    return 0
                # PR creation failed/cancelled -- fall through to the
                # top-level menu again so the user can retry or end.
            else:
                print("Goodbye.")
                return 0
        elif choice == "2":
            if _create_pr_flow_interactive(repo_path, repo_url, issue_text, issue_id, result, trajectory):
                print("Goodbye.")
                return 0
        elif choice == "3":
            print("Ending. No PR created.")
            return 0
        else:
            print("Please choose 1, 2, or 3.")


def interactive_run() -> int:
    """Part A + B: no-flags entry point. Setup happens up front (repo, issue,
    a single confirmation gate), the phase loop then runs fully uninterrupted
    to a final state, and only then does a post-run menu appear."""
    print("=== Sutra interactive setup ===")
    repo_url = _prompt_repo()
    issue_text = _prompt_issue(repo_url)
    if not _confirm_start(repo_url, issue_text):
        return 0

    issue_id = f"interactive-{uuid.uuid4().hex[:8]}"
    result, repo_path, trajectory = _clone_and_run_interactive(repo_url, issue_text, issue_id)
    if result is None:
        return 1

    _print_run_summary(result)
    return _post_run_menu(repo_path, repo_url, issue_text, issue_id, result, trajectory)


def main() -> int:
    if len(sys.argv) == 1:
        return interactive_run()

    parser = argparse.ArgumentParser(description="Phase 0-4 demo driver")

    # --- live arbitrary-repo mode ---
    parser.add_argument(
        "--repo",
        default=None,
        help="GitHub URL or local path of the target repo to run against (enables live mode)",
    )
    parser.add_argument(
        "--issue-file",
        default=None,
        help="path to a plain-text file containing the issue title + body (required with --repo)",
    )
    parser.add_argument(
        "--create-pr",
        action="store_true",
        help="open a real GitHub pull request with the fix if the run is verified (--repo mode only). "
        "Requires GITHUB_TOKEN, --allow-pr-target, and --confirm-pr. Never triggers on an unresolved run.",
    )
    parser.add_argument(
        "--allow-pr-target",
        default=None,
        help="required with --create-pr: the exact repo URL PR creation is allowed to target. Must match "
        "--repo. There is no default-allowed target -- this exists so --create-pr can never fire "
        "against an unintended repo.",
    )
    parser.add_argument(
        "--confirm-pr",
        action="store_true",
        help="required with --create-pr, in addition to --allow-pr-target: a second, deliberate opt-in "
        "so a copy-pasted command can't open a real PR by accident.",
    )

    # --- fixture-issue mode (original) ---
    parser.add_argument("--issue", choices=["1", "2", "3"], default="1", help="which real issue to solve (default: 1)")
    parser.add_argument(
        "--naive-baseline",
        action="store_true",
        help="control condition: skip triage, always use the largest budget profile, never force an "
        "early phase transition. Works with fixture issue #1 and with --repo live mode.",
    )
    parser.add_argument(
        "--reset-memory",
        action="store_true",
        help="wipe this repo's memory bank before running -- use before issue #1 for a genuinely "
        "fresh/empty memory bank in the two-issue demo.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="override the reflect/retry allowance (default: 1). Issue #3 defaults to 0, to exercise "
        "Part A's calibrated-abstention path on a real, unforced gate failure.",
    )
    args = parser.parse_args()

    # --- Route to live mode if --repo is given ---
    if args.repo:
        if not args.issue_file:
            parser.error("--repo requires --issue-file <path/to/issue.txt>")
        max_retries = args.max_retries if args.max_retries is not None else MAX_REFLECT_RETRIES
        return live_repo_run(
            repo_url=args.repo,
            issue_file=args.issue_file,
            max_retries=max_retries,
            reset_memory=args.reset_memory,
            naive_baseline=args.naive_baseline,
            create_pr=args.create_pr,
            allow_pr_target=args.allow_pr_target,
            confirm_pr=args.confirm_pr,
        )

    # --- Original fixture mode ---
    if args.create_pr:
        parser.error("--create-pr only applies to --repo mode (there's no real remote to open a PR against in fixture mode)")
    issue = ISSUES[args.issue]
    if args.naive_baseline and issue["naive_mock_builder"] is None:
        parser.error(f"--naive-baseline has no control script for issue {args.issue}")
    max_retries = args.max_retries if args.max_retries is not None else issue["default_max_retries"]

    print(f"[demo] preparing sandbox for issue #{args.issue} (cherry-picked regression tests)...")
    repo_path, base_commit = prepare_sandbox(issue["base_commit"], issue["test_commit"], issue["commit_message"])
    print(f"[demo] sandbox ready at {repo_path}, base_commit={base_commit[:10]}")

    if args.reset_memory:
        repo_id = repo_id_for(repo_path)
        memory_path = os.path.join(STORE_DIR, f"{repo_id}.json")
        if os.path.exists(memory_path):
            os.remove(memory_path)
            print(f"[demo] wiped repo memory at {memory_path}")

    adapter = _detect_live_adapter()
    if adapter is None:
        if args.naive_baseline:
            print("[demo] using scripted MockAdapter (naive-baseline control script)")
            adapter = issue["naive_mock_builder"](repo_path)
        else:
            print("[demo] using scripted MockAdapter (deterministic replay)")
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

    orchestrator = Orchestrator(
        adapter, config, trajectory, naive_baseline=args.naive_baseline,
        max_retries=max_retries if max_retries is not None else MAX_REFLECT_RETRIES,
    )
    result = orchestrator.run()

    print()
    print("=" * 70)
    print(f"STATUS: {result.status}   tier={result.tier}")
    print(f"triage justification: {result.triage_justification}")
    for name, g in result.gates.items():
        status = "PASS" if g["passed"] else "FAIL"
        print(f"  [{status}] {name}: {g['detail']}")
    print(f"retries_used={result.retries_used}  total_tokens={result.total_tokens}  total_tool_calls={result.total_tool_calls}")
    if result.status == "unresolved":
        print(f"best_checkpoint={result.best_checkpoint}")
        print(f"confidence_report={result.confidence_report}")
    if result.adversarial_review:
        print(f"adversarial_review={result.adversarial_review}")
    print("=" * 70)
    print()
    print("--- final diff (best checkpoint) ---")
    print(result.diff)
    print(f"[demo] trajectory log written to {trajectory_path}")
    print(f"[demo] sandbox repo left at {repo_path} for inspection")

    return 0 if result.verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
