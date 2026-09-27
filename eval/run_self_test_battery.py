#!/usr/bin/env python3
"""Runner for Sutra's 10-Case Self-Test Battery.

Evaluates Sutra across 10 critical operational dimensions:
  1. Sanity baseline (easy, unambiguous)
  2. Multi-site bug / regression gate (looks simple, touches two places)
  3. No pre-existing reproduction test (prose-only spec)
  4. Genuinely too hard within budget (honest Phase 4 abstention)
  5. Issue already fixed / not reproducible (non-bug)
  6. Misleading issue description (wrong file named)
  7. Different repo entirely (not more-itertools)
  8. Larger real-world file (200+ line production file)
  9. Empty or malformed issue (low-information input)
  10. Prescribed model verification (live adapter end-to-end)

Usage:
  python eval/run_self_test_battery.py --all
  python eval/run_self_test_battery.py --mock --all
  python eval/run_self_test_battery.py --case 1,2,4
  python eval/run_self_test_battery.py --live --case 1
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from typing import Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from harness.config import get_eval_model
from harness.memory.trajectory_store import TrajectoryStore
from harness.model_adapter.base import ModelAdapter
from harness.model_adapter.mock_adapter import MockAdapter
from harness.orchestrator.state_machine import MAX_REFLECT_RETRIES, Orchestrator, RunConfig, RunResult
from harness.sandbox.local_sandbox import LocalSandbox
from demo.run_demo import (
    _CB_NEW_ANCHOR,
    _CB_OLD_ANCHOR,
    _detect_live_adapter,
    _extract_one_only_block,
    _one_only_fixed_blocks,
    _run_orchestrator_safely,
    build_issue1_mock_adapter,
    build_issue2_mock_adapter,
    build_issue3_mock_adapter,
)

BATTERY_DIR = os.path.join(ROOT_DIR, "eval", "self_test_battery")
RESULTS_DIR = os.path.join(BATTERY_DIR, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


CASE_DIRS = [
    "01_easy_unambiguous",
    "02_touches_two_places",
    "03_no_reproduction_test",
    "04_too_hard_budget",
    "05_already_fixed",
    "06_misleading_description",
    "07_different_repo",
    "08_larger_real_world_file",
    "09_empty_or_malformed",
    "10_prescribed_model",
]


def _build_case_mock_adapter(case_id: int, repo_path: str) -> MockAdapter:
    """Provides deterministic scripted mock behaviors for mock-mode testing."""
    if case_id == 1:
        return build_issue2_mock_adapter()
    elif case_id == 2:
        return build_issue1_mock_adapter(repo_path)
    elif case_id == 3:
        # Case 3: distinct_combinations without target test
        script = [
            {"content": '{"tier": "trivial", "justification": "A missing check on a single parameter r."}'},
            {"content": "Searching for distinct_combinations in more_itertools.", "tool": {"name": "search_symbol", "arguments": {"symbol_name": "distinct_combinations", "path": "more_itertools"}}},
            {"content": "Found distinct_combinations in more_itertools/more.py. Reading lines.", "tool": {"name": "open_file", "arguments": {"path": "more_itertools/more.py", "start_line": 2860, "end_line": 2880}}},
            {"content": "Plan: add `if r < 0: return` at top of distinct_combinations generator."},
            {"content": "Applying check.", "tool": {"name": "edit_file", "arguments": {"path": "more_itertools/more.py", "old_str": "    if r < 0:\n        return\n", "new_str": "    if r < 0:\n        return\n"}}},
            {"content": "Running regression tests.", "tool": {"name": "run_tests", "arguments": {"test_path": "tests/test_more.py::DistinctCombinationsTests"}}},
            {"content": "Regression tests pass. Verification complete."},
            {"content": '{"conventions": [], "landmines": [], "fix_patterns": []}'},
            {"content": '{"verdict": "no_concerns", "notes": "Clean guard added for negative r; regression tests pass."}'},
        ]
        return MockAdapter(script)
    elif case_id == 4:
        return build_issue3_mock_adapter()
    elif case_id == 5:
        # Case 5: already fixed / not reproducible
        script = [
            {"content": '{"tier": "trivial", "justification": "Verifying reported behavior against test suite."}'},
            {"content": "Checking collate() in more_itertools/more.py.", "tool": {"name": "search_symbol", "arguments": {"symbol_name": "collate", "path": "more_itertools"}}},
            {"content": "Running existing collate tests to check if empty inputs fail.", "tool": {"name": "run_tests", "arguments": {"test_path": "tests/test_more.py::CollateTests"}}},
            {"content": "CollateTests pass without error. heapq.merge already handles empty streams. No code change needed."},
            {"content": '{"conventions": [], "landmines": [], "fix_patterns": []}'},
            {"content": '{"verdict": "no_concerns", "notes": "Reported bug does not reproduce; existing tests pass."}'},
        ]
        return MockAdapter(script)
    elif case_id == 6:
        # Case 6: misleading description (points to recipes.py, actually in more.py)
        script = [
            {"content": '{"tier": "moderate", "justification": "Validating parameter max_count in constrained_batches."}'},
            {"content": "Opening recipes.py as mentioned in issue.", "tool": {"name": "open_file", "arguments": {"path": "more_itertools/recipes.py", "start_line": 1, "end_line": 60}}},
            {"content": "constrained_batches is not in recipes.py! Searching symbols across the repository.", "tool": {"name": "search_symbol", "arguments": {"symbol_name": "constrained_batches", "path": "more_itertools"}}},
            {"content": "Found constrained_batches in more_itertools/more.py. Applying validation fix.", "tool": {"name": "edit_file", "arguments": {"path": "more_itertools/more.py", "old_str": _CB_OLD_ANCHOR, "new_str": _CB_NEW_ANCHOR}}},
            {"content": "Running regression tests.", "tool": {"name": "run_tests", "arguments": {"test_path": "tests/test_more.py::ConstrainedBatchesTests"}}},
            {"content": "Tests pass. Successfully avoided misleading file reference."},
            {"content": '{"conventions": [], "landmines": [], "fix_patterns": []}'},
            {"content": '{"verdict": "no_concerns", "notes": "Correctly localized to more.py and fixed max_count validation."}'},
        ]
        return MockAdapter(script)
    elif case_id == 7:
        # Case 7: different repo (strutils)
        old_slug_line = "    text = re.sub(r\"\\s\", separator, text)\n"
        new_slug_line = "    text = re.sub(r\"\\s+\", separator, text)\n"
        script = [
            {"content": '{"tier": "trivial", "justification": "Fixing regex in slugify to collapse consecutive whitespace."}'},
            {"content": "Opening strutils/slug.py.", "tool": {"name": "open_file", "arguments": {"path": "strutils/slug.py", "start_line": 1, "end_line": 25}}},
            {"content": "Plan: replace `\\s` with `\\s+` in re.sub to collapse consecutive whitespace.", "tool": {"name": "edit_file", "arguments": {"path": "strutils/slug.py", "old_str": old_slug_line, "new_str": new_slug_line}}},
            {"content": "Running test suite.", "tool": {"name": "run_tests", "arguments": {"test_path": "tests/test_slug.py"}}},
            {"content": "All tests pass."},
            {"content": '{"conventions": [], "landmines": [], "fix_patterns": []}'},
            {"content": '{"verdict": "no_concerns", "notes": "Regex updated to collapse whitespace correctly."}'},
        ]
        return MockAdapter(script)
    elif case_id == 8:
        # Case 8: larger real-world file
        script = [
            {"content": '{"tier": "moderate", "justification": "Preserving search query state across navigation in Search page."}'},
            {"content": "Listing files.", "tool": {"name": "search_code", "arguments": {"query": "Search", "path": "."}}},
            {"content": "Plan: verify state persistence pattern without blowing context."},
            {"content": '{"conventions": [], "landmines": [], "fix_patterns": []}'},
            {"content": '{"verdict": "no_concerns", "notes": "Handled large file structure cleanly."}'},
        ]
        return MockAdapter(script)
    elif case_id == 9:
        # Case 9: empty or malformed issue
        script = [
            {"content": '{"tier": "moderate", "justification": "Underspecified issue description: cannot isolate component."}'},
            {"content": "Checking repo status and recent tests.", "tool": {"name": "run_tests", "arguments": {}}},
            {"content": "Issue is too underspecified to safely apply code changes without risking regressions."},
            {"content": '{"conventions": [], "landmines": [], "fix_patterns": []}'},
            {"content": '{"root_cause_confidence": "low", "root_cause_summary": "Issue description was underspecified.", "unresolved_issue": "Insufficient information to reproduce."}'},
        ]
        return MockAdapter(script)
    elif case_id == 10:
        return build_issue2_mock_adapter()

    return MockAdapter([{"content": '{"tier": "trivial", "justification": "default"}'}])


def prepare_battery_sandbox(target_info: dict[str, Any]) -> tuple[str, str]:
    """Sets up a clean sandbox for the battery case and returns (repo_path, base_commit)."""
    repo_type = target_info.get("repo_type", "git_local")
    run_id = uuid.uuid4().hex[:8]
    temp_dir = tempfile.mkdtemp(prefix=f"sutra-battery-{run_id}-")

    if repo_type == "git_remote":
        repo_url = target_info["repo_source"]
        subprocess.run(["git", "clone", "--quiet", "--depth=50", repo_url, temp_dir], check=True)
        subprocess.run(["git", "config", "user.email", "agent@harness.local"], cwd=temp_dir, check=True)
        subprocess.run(["git", "config", "user.name", "SWE Agent Harness"], cwd=temp_dir, check=True)
        base_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=temp_dir, capture_output=True, text=True, check=True
        ).stdout.strip()
        return temp_dir, base_commit

    repo_source = os.path.join(ROOT_DIR, target_info["repo_source"])
    base_commit = target_info.get("base_commit", "HEAD")
    test_commit = target_info.get("test_commit")

    has_git = os.path.isdir(os.path.join(repo_source, ".git"))
    if has_git:
        subprocess.run(["git", "clone", "--quiet", repo_source, temp_dir], check=True)
    else:
        shutil.copytree(repo_source, temp_dir, dirs_exist_ok=True)
        subprocess.run(["git", "init", "--quiet"], cwd=temp_dir, check=True)
        subprocess.run(["git", "config", "user.email", "agent@harness.local"], cwd=temp_dir, check=True)
        subprocess.run(["git", "config", "user.name", "SWE Agent Harness"], cwd=temp_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=temp_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit", "--quiet"], cwd=temp_dir, check=True)

    subprocess.run(["git", "config", "user.email", "agent@harness.local"], cwd=temp_dir, check=True)
    subprocess.run(["git", "config", "user.name", "SWE Agent Harness"], cwd=temp_dir, check=True)

    if has_git and base_commit and base_commit != "HEAD":
        subprocess.run(["git", "checkout", "--quiet", base_commit], cwd=temp_dir, check=True)

    if test_commit:
        # Cherry-pick test commit and restore non-test files
        subprocess.run(["git", "cherry-pick", "-n", test_commit], cwd=temp_dir, check=True)
        changed = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"], cwd=temp_dir, capture_output=True, text=True, check=True
        ).stdout.split()
        non_test = [f for f in changed if not f.startswith("tests/")]
        if non_test:
            subprocess.run(["git", "checkout", base_commit, "--", *non_test], cwd=temp_dir, check=True)
        subprocess.run(["git", "commit", "-m", "test: add regression tests"], cwd=temp_dir, check=True)

    new_base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=temp_dir, capture_output=True, text=True, check=True
    ).stdout.strip()
    return temp_dir, new_base


def evaluate_battery_case(
    case_num: int,
    case_dir_name: str,
    use_mock: bool,
    live_adapter: ModelAdapter | None = None,
    naive_baseline: bool = False,
) -> dict[str, Any]:
    """Runs a single test case from the battery and returns its evaluation record."""
    case_path = os.path.join(BATTERY_DIR, case_dir_name)
    with open(os.path.join(case_path, "target_repo.json"), encoding="utf-8") as f:
        target_info = json.load(f)
    with open(os.path.join(case_path, "issue.txt"), encoding="utf-8") as f:
        issue_text = f.read().strip()

    name = target_info.get("name", case_dir_name)
    expected_status = target_info.get("expected_status", "verified")
    max_retries = target_info.get("max_retries", 3)

    print(f"\n[{case_num}/10] Running Category: {name} ...")

    repo_path, base_commit = prepare_battery_sandbox(target_info)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    traj_filename = f"{case_dir_name}_{timestamp}.jsonl"
    traj_path = os.path.join(RESULTS_DIR, traj_filename)
    trajectory = TrajectoryStore(traj_path)

    config = RunConfig(
        repo_path=repo_path,
        base_commit=base_commit,
        issue_text=issue_text,
        target_test=target_info.get("target_test"),
        regression_test_paths=target_info.get("regression_test_paths"),
        issue_id=target_info.get("issue_id", f"battery-case-{case_num}"),
    )

    if use_mock:
        adapter = _build_case_mock_adapter(case_num, repo_path)
    else:
        adapter = live_adapter or _detect_live_adapter()

    try:
        orchestrator = Orchestrator(
            adapter,
            config,
            trajectory,
            naive_baseline=naive_baseline,
            max_retries=max_retries,
        )
        result = _run_orchestrator_safely(orchestrator)
    except Exception as e:
        print(f"  [ERROR] Uncaught exception in orchestrator: {e}")
        result = None

    # Evaluate pass/fail against category expectation
    outcome = "FAIL"
    reason = ""

    if result is None:
        outcome = "FAIL"
        reason = "Orchestrator returned None or crashed"
        actual_status = "crashed"
        tokens = 0
        retries = 0
        tier = "unknown"
    else:
        actual_status = result.status
        tokens = result.total_tokens
        retries = result.retries_used
        tier = result.tier or "moderate"

        if case_num == 1:
            # Easy, unambiguous: verified
            if result.status == "verified" and result.verified:
                outcome = "PASS"
                reason = "Resolved cleanly and verified on first try"
            else:
                reason = f"Expected verified, got {result.status}"
        elif case_num == 2:
            # Touches two places: verified, caught regression
            if result.status == "verified" and result.verified:
                outcome = "PASS"
                reason = "Multi-location fix verified; regression gate satisfied"
            else:
                reason = f"Expected verified, got {result.status}"
        elif case_num == 3:
            # No reproduction test: verified without target gate or clean diff
            if result.status in ("verified", "unresolved") and not result.gates.get("target_test", {}).get("error"):
                outcome = "PASS"
                reason = "Gracefully handled absent target test gate with honest reporting"
            else:
                reason = "Crashed or failed on missing target test"
        elif case_num == 4:
            # Genuinely too hard: honest abstention
            if result.status == "unresolved" and result.confidence_report is not None:
                outcome = "PASS"
                reason = "Honest Phase 4 abstention with confidence report"
            else:
                reason = f"Expected unresolved with confidence report, got status={result.status}"
        elif case_num == 5:
            # Already fixed / non-bug: no destructive edits, clean completion
            if result.status in ("verified", "unresolved") and not result.gates.get("regression_tests", {}).get("failed"):
                outcome = "PASS"
                reason = "Existing tests continue to pass; did not fabricate breaking changes"
            else:
                reason = f"Failed regression gate on non-bug issue: {result.status}"
        elif case_num == 6:
            # Misleading description: localized to correct file
            if result.status == "verified" and ("more_itertools/recipes.py" not in result.diff):
                outcome = "PASS"
                reason = "Avoided misleading file trap; edited correct module"
            else:
                reason = f"Status={result.status}; diff modified wrong file or failed"
        elif case_num == 7:
            # Different repo entirely: verified on strutils
            if result.status == "verified":
                outcome = "PASS"
                reason = "Successfully resolved bug in new, unseen repository"
            else:
                reason = f"Expected verified on new repo, got {result.status}"
        elif case_num == 8:
            # Larger real-world file: handled without blowout
            if result.status in ("verified", "unresolved"):
                outcome = "PASS"
                reason = "Successfully handled large file without token or context errors"
            else:
                reason = "Failed with context error"
        elif case_num == 9:
            # Empty or malformed: graceful exit / honest abstention
            if result.status in ("verified", "unresolved"):
                outcome = "PASS"
                reason = "Handled underspecified input without crashing or spinning"
            else:
                reason = f"Crashed or failed on malformed issue: {result.status}"
        elif case_num == 10:
            # Prescribed model: verified
            if result.status == "verified":
                outcome = "PASS"
                reason = "End-to-end execution succeeded on configured model"
            else:
                reason = f"Status={result.status}"

    print(f"  Result: {outcome} ({reason}) | Status: {actual_status} | Tier: {tier} | Tokens: {tokens}")
    shutil.rmtree(repo_path, ignore_errors=True)

    return {
        "case_num": case_num,
        "category": case_dir_name,
        "name": name,
        "expected_status": expected_status,
        "actual_status": actual_status,
        "outcome": outcome,
        "reason": reason,
        "tier": tier,
        "tokens": tokens,
        "retries": retries,
        "trajectory": traj_filename,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sutra Self-Test Battery Runner")
    parser.add_argument("--all", action="store_true", help="Run all 10 cases")
    parser.add_argument("--case", type=str, default=None, help="Comma-separated case numbers to run (e.g. 1,2,4)")
    parser.add_argument("--mock", action="store_true", help="Run in deterministic mock mode (fast, zero tokens)")
    parser.add_argument("--live", action="store_true", help="Force live LLM adapter mode using AI_API_KEY")
    parser.add_argument(
        "--naive-baseline",
        action="store_true",
        help="Use naive baseline mode (complex budget profile, no early phase ejection)",
    )
    args = parser.parse_args()

    # Determine cases to run
    if args.case:
        selected_indices = [int(x.strip()) for x in args.case.split(",") if x.strip().isdigit()]
    elif args.all or not sys.argv[1:]:
        selected_indices = list(range(1, 11))
    else:
        selected_indices = list(range(1, 11))

    # Detect adapter mode
    has_key = bool(os.environ.get("AI_API_KEY")) or bool(os.environ.get("GEMINI_API_KEY"))
    use_mock = args.mock or (not has_key and not args.live)

    print("=" * 80)
    print("SUTRA SELF-TEST BATTERY (10 Operational Dimensions)")
    print(f"Mode: {'MOCK (Deterministic Scripts)' if use_mock else 'LIVE ADAPTER'}")
    if not use_mock:
        print(f"Configured Model: {get_eval_model()}")
    print(f"Naive Baseline Headroom: {args.naive_baseline}")
    print(f"Running cases: {selected_indices}")
    print(f"Trajectories saved to: {RESULTS_DIR}")
    print("=" * 80)

    live_adapter = None
    if not use_mock:
        live_adapter = _detect_live_adapter()

    results: list[dict[str, Any]] = []
    for idx in selected_indices:
        if 1 <= idx <= len(CASE_DIRS):
            res = evaluate_battery_case(
                idx,
                CASE_DIRS[idx - 1],
                use_mock=use_mock,
                live_adapter=live_adapter,
                naive_baseline=args.naive_baseline,
            )
            results.append(res)

    # Print Summary Table
    print("\n" + "=" * 105)
    print("BATTERY EVALUATION SUMMARY TABLE")
    print("=" * 105)
    header = f"{'#':<3} | {'Category':<28} | {'Tier':<10} | {'Expected':<12} | {'Actual':<10} | {'Outcome':<7} | {'Tokens':<7} | {'Retries':<7}"
    print(header)
    print("-" * 105)
    passed_count = 0
    for r in results:
        is_pass = r["outcome"] == "PASS"
        if is_pass:
            passed_count += 1
        line = (
            f"{r['case_num']:<3} | "
            f"{r['category']:<28} | "
            f"{r['tier']:<10} | "
            f"{r['expected_status'][:12]:<12} | "
            f"{r['actual_status'][:10]:<10} | "
            f"{r['outcome']:<7} | "
            f"{r['tokens']:<7} | "
            f"{r['retries']:<7}"
        )
        print(line)
    print("-" * 105)
    print(f"Final Score: {passed_count}/{len(results)} passed ({passed_count / len(results) * 100:.1f}%)")
    print(f"Detailed trajectories available in: {RESULTS_DIR}")
    print("Inspect any run in the Timeline UI: http://localhost:8008/?run=<filename-without-jsonl>")
    print("=" * 105)

    return 0 if passed_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
