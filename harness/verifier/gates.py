"""Verification gates, run in order. Every submission must clear all four
before being called "verified" -- and the pass/fail breakdown is always
surfaced (to the trajectory log / UI), never hidden.

The patch itself is never parsed out of the model's chat reply: the agent
edits files in place inside the sandbox, and `git diff` against the base
commit at submission time is the only source of truth for what changed.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any

from harness.tools import git_ops, test_runner


def run_gates(
    repo_path: str,
    base_commit: str,
    target_test: str | None,
    regression_test_paths: list[str] | None = None,
) -> dict[str, Any]:
    gates: dict[str, dict[str, Any]] = {}

    gates["patch_applies"] = _gate_patch_applies(repo_path, base_commit)
    if not gates["patch_applies"]["passed"]:
        return _finish(gates)

    gates["builds"] = _gate_builds(repo_path)
    if not gates["builds"]["passed"]:
        return _finish(gates)

    gates["target_test_passes"] = _gate_target_test(repo_path, target_test)
    if not gates["target_test_passes"]["passed"]:
        return _finish(gates)

    gates["regression_subset_passes"] = _gate_regression(repo_path, regression_test_paths)
    return _finish(gates)


def _finish(gates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    all_passed = all(g["passed"] for g in gates.values())
    return {"verified": all_passed, "gates": gates}


def _gate_patch_applies(repo_path: str, base_commit: str) -> dict[str, Any]:
    diff = git_ops.git_diff(repo_path, base_ref=base_commit)
    if not diff.get("ok"):
        return {"passed": False, "detail": diff.get("error", "git diff failed")}
    if not diff["has_changes"]:
        return {"passed": False, "detail": "no changes were made -- nothing to verify"}
    return {"passed": True, "detail": f"{len(diff['diff'].splitlines())} diff lines against base {base_commit[:8]}"}


def _gate_builds(repo_path: str) -> dict[str, Any]:
    diff = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=repo_path, capture_output=True, text=True
    )
    changed_py = [f for f in diff.stdout.splitlines() if f.endswith(".py")]
    if not changed_py:
        return {"passed": True, "detail": "no changed .py files to compile-check"}

    for f in changed_py:
        proc = subprocess.run([sys.executable, "-m", "py_compile", f], cwd=repo_path, capture_output=True, text=True)
        if proc.returncode != 0:
            return {"passed": False, "detail": f"{f} fails to compile: {proc.stderr.strip()[:500]}"}
    return {"passed": True, "detail": f"{len(changed_py)} changed file(s) compile cleanly"}


def _gate_target_test(repo_path: str, target_test: str | None) -> dict[str, Any]:
    if not target_test:
        return {"passed": True, "detail": "no target_test specified, skipped"}
    result = test_runner.run_tests(repo_path, test_path=target_test)
    if not result.get("ok"):
        return {"passed": False, "detail": result.get("error", "test run failed")}
    return {"passed": result["passed"], "detail": result["summary"]}


def _gate_regression(repo_path: str, regression_test_paths: list[str] | None) -> dict[str, Any]:
    if not regression_test_paths:
        return {"passed": True, "detail": "no regression subset specified, skipped"}
    for path in regression_test_paths:
        result = test_runner.run_tests(repo_path, test_path=path)
        if not result.get("ok") or not result["passed"]:
            detail = result.get("error") or result.get("summary", "failed")
            return {"passed": False, "detail": f"{path}: {detail}"}
    return {"passed": True, "detail": f"{len(regression_test_paths)} regression path(s) passed"}
