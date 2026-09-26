"""Subprocess + tempdir sandbox: clones the target repo into an isolated
directory and checks out the base commit. This is the fallback used whenever
Docker isn't available -- and in this environment, Docker's daemon isn't
running, so this is the active path."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass


@dataclass
class SandboxHandle:
    repo_path: str
    base_commit: str

    def cleanup(self) -> None:
        shutil.rmtree(self.repo_path, ignore_errors=True)


class LocalSandbox:
    """One instance per repo/issue run. Resource limits are enforced per-command
    by the tools (bash.py, test_runner.py) via rlimits, not at the sandbox level,
    since there's no container boundary here."""

    def __init__(self, work_root: str | None = None):
        self.work_root = work_root

    def create(self, repo_source: str, base_commit: str, run_id: str) -> SandboxHandle:
        """repo_source: local path or git URL. Clones/copies it and checks out base_commit."""
        repo_path = tempfile.mkdtemp(prefix=f"agent-run-{run_id}-", dir=self.work_root)

        if repo_source.startswith(("http://", "https://", "git@")):
            subprocess.run(["git", "clone", "--quiet", repo_source, repo_path], check=True)
        else:
            subprocess.run(["git", "clone", "--quiet", repo_source, repo_path], check=True)

        subprocess.run(["git", "checkout", "--quiet", base_commit], cwd=repo_path, check=True)
        subprocess.run(["git", "config", "user.email", "agent@harness.local"], cwd=repo_path, check=True)
        subprocess.run(["git", "config", "user.name", "SWE Agent Harness"], cwd=repo_path, check=True)

        return SandboxHandle(repo_path=repo_path, base_commit=base_commit)
