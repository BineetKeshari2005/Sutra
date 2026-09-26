"""git_diff / git_checkpoint / git_reset -- checkpointing inside the sandboxed
repo clone. This is the ONLY source of truth for what the agent submitted:
the verifier reads `git diff` against the base commit, never the model's
chat reply."""
from __future__ import annotations

import subprocess
from typing import Any


def _run(repo_path: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True)


def git_diff(repo_path: str, base_ref: str | None = None) -> dict[str, Any]:
    args = ["diff", base_ref] if base_ref else ["diff", "HEAD"]
    proc = _run(repo_path, args)
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    return {"ok": True, "diff": proc.stdout, "has_changes": bool(proc.stdout.strip())}


def git_checkpoint(repo_path: str, message: str) -> dict[str, Any]:
    _run(repo_path, ["add", "-A"])
    status = _run(repo_path, ["status", "--porcelain"])
    if not status.stdout.strip():
        return {"ok": True, "committed": False, "hint": "no changes to checkpoint"}

    proc = _run(repo_path, ["commit", "-m", message])
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    sha = _run(repo_path, ["rev-parse", "HEAD"]).stdout.strip()
    return {"ok": True, "committed": True, "commit": sha}


def git_reset(repo_path: str, to: str = "HEAD") -> dict[str, Any]:
    proc = _run(repo_path, ["reset", "--hard", to])
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    return {"ok": True, "reset_to": to}


def show_file_at_commit(repo_path: str, commit: str, path: str) -> str | None:
    """The pristine content of `path` as of `commit`, regardless of what the
    working tree looks like now (used to build the repo-memory symbol index
    from the state the agent actually started from, not its own edits)."""
    proc = _run(repo_path, ["show", f"{commit}:{path}"])
    if proc.returncode != 0:
        return None
    return proc.stdout
