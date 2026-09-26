"""Opens a real GitHub Pull Request for a verified fix.

Uses only the standard library (urllib + subprocess) -- no new dependency.
This module never runs on its own: it's called from demo/run_demo.py only
when the user passes --create-pr explicitly and the run's status is
"verified". Pushing branches and opening PRs are visible, hard-to-reverse
actions, so this stays an explicit, opt-in step -- never triggered by
default, never for an "unresolved" run.

Two independent opt-ins are required before anything is actually pushed,
enforced inside create_pull_request() itself (not just by the CLI, so
calling this function directly can never skip the check):
  1. --allow-pr-target <repo-url> must match the run's target repo exactly
     -- there is no default-allowed target.
  2. --confirm-pr must also be passed in the same invocation.
Every attempt is logged as a pr_creation_attempt trajectory event, whether
it proceeds or is refused, so it's auditable after the fact either way.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

API_ROOT = "https://api.github.com"
FORK_POLL_ATTEMPTS = 10
FORK_POLL_DELAY_SECONDS = 2


class GitHubPRError(RuntimeError):
    """Raised for any failure in the fork/push/PR flow. Never includes the token."""


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _api_request(method: str, path: str, token: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{API_ROOT}{path}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise GitHubPRError(f"GitHub API {method} {path} failed: {e.code} {e.reason} -- {detail[:500]}") from None
    except urllib.error.URLError as e:
        raise GitHubPRError(f"GitHub API {method} {path} unreachable: {e.reason}") from None


def _run_git(repo_path: str, args: list[str], token: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitHubPRError(_redact(f"git {' '.join(args)} failed: {proc.stderr.strip()}", token))
    return proc


def _parse_owner_repo(repo_url: str) -> tuple[str, str]:
    parsed = urlparse(repo_url if "://" in repo_url else f"https://{repo_url}")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise GitHubPRError(f"could not parse owner/repo from '{repo_url}'")
    owner, repo = parts[0], parts[1]
    return owner, repo.removesuffix(".git")


def _extract_issue_number(issue_text: str, issue_id: str) -> int | None:
    m = re.search(r"#(\d+)", issue_text) or re.search(r"issue[-_ ]?(\d+)", issue_id, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _changed_files(diff_text: str) -> list[str]:
    return re.findall(r"^diff --git a/(.+?) b/.+$", diff_text, re.MULTILINE)


def check_pr_authorization(
    repo_url: str, allowed_target: str | None, confirmed: bool, interactive: bool = False
) -> tuple[bool, str]:
    """Pure, no-network authorization check -- deliberately separate from
    create_pull_request() so it's trivial to unit test every refusal path
    without touching the GitHub API. There is no default-on path: a missing
    or mismatched --allow-pr-target refuses, and a missing --confirm-pr
    refuses even when the target is allowlisted.

    `interactive=True` is the one alternate path: the interactive menu flow's
    two affirmative actions (explicitly choosing "Create PR" from a menu, then
    explicitly typing a token when prompted) stand in for --allow-pr-target
    and --confirm-pr. This substitution applies ONLY when the caller is the
    interactive flow -- the flag-based path never sets this, so it keeps
    requiring both flags exactly as before."""
    if interactive:
        return True, "interactive mode: explicit menu choice + token entry"

    if not allowed_target:
        return False, "no --allow-pr-target supplied -- refusing (there is no default-allowed target)"

    try:
        target_owner, target_repo = _parse_owner_repo(repo_url)
    except GitHubPRError as e:
        return False, f"could not parse target repo URL: {e}"
    try:
        allowed_owner, allowed_repo = _parse_owner_repo(allowed_target)
    except GitHubPRError as e:
        return False, f"could not parse --allow-pr-target: {e}"

    if (target_owner.lower(), target_repo.lower()) != (allowed_owner.lower(), allowed_repo.lower()):
        return False, (
            f"target repo '{target_owner}/{target_repo}' does not match "
            f"--allow-pr-target '{allowed_owner}/{allowed_repo}' -- refusing"
        )

    if not confirmed:
        return False, "target is allowlisted but --confirm-pr was not passed -- refusing"

    return True, "allowlisted and confirmed"


def _compose_pr_body(issue_text: str, issue_number: int | None, result: Any) -> str:
    files = _changed_files(result.diff) or ["(see diff)"]
    files_block = "\n".join(f"- `{f}`" for f in files)

    gate_lines = []
    for name, g in result.gates.items():
        mark = "x" if g.get("passed") else " "
        gate_lines.append(f"- [{mark}] `{name}` -- {g.get('detail', '')}")
    gates_block = "\n".join(gate_lines) if gate_lines else "- (no gates recorded)"

    review_block = ""
    if result.adversarial_review:
        review_block = (
            f"\n**Independent review** (blind to this agent's own reasoning, given only the "
            f"issue and diff): `{result.adversarial_review['verdict']}` -- "
            f"{result.adversarial_review['notes']}\n"
        )

    fixes_line = f"Fixes #{issue_number}\n\n" if issue_number is not None else ""
    problem_summary = issue_text.strip().splitlines()[0] if issue_text.strip() else "(see linked issue)"

    return f"""{fixes_line}## Problem summary
{problem_summary}

## Root cause & fix
{result.triage_justification}
{review_block}
## Files changed
{files_block}

## Verification
{gates_block}

---
_Generated autonomously by [Sutra-AI](https://github.com/BineetKeshari2005/Sutra) -- review this diff like any other PR before merging._
"""


def create_pull_request(
    repo_path: str,
    repo_url: str,
    issue_text: str,
    issue_id: str,
    result: Any,
    github_token: str,
    *,
    allowed_target: str | None = None,
    confirmed: bool = False,
    interactive: bool = False,
    trajectory: Any = None,
) -> str:
    """Pushes the fix already committed in `repo_path` as a branch and opens a
    PR against `repo_url`. Forks automatically if the token's user lacks push
    access. Returns the PR's html_url.

    Refuses unless authorized -- checked here, not just by the CLI layer that
    calls this, so calling this function directly can never skip the check.
    Authorization is either `repo_url` matching `allowed_target` AND
    `confirmed` being True (the flag-based path), or `interactive=True` (the
    menu-driven path's own two affirmative actions -- see
    check_pr_authorization). Every attempt (blocked or not) is logged to
    `trajectory` if one is given, noting which mode triggered it, so a PR
    attempt is always auditable after the fact even when it was refused.
    """
    authorized, reason = check_pr_authorization(repo_url, allowed_target, confirmed, interactive)
    if trajectory is not None:
        trajectory.append(
            "finalize",
            "pr_creation_attempt",
            {
                "mode": "interactive" if interactive else "flag",
                "repo_url": repo_url,
                "allowed_target": allowed_target,
                "confirmed": confirmed,
                "authorized": authorized,
                "reason": reason,
            },
        )
    if not authorized:
        raise GitHubPRError(f"PR creation refused: {reason}")

    owner, repo = _parse_owner_repo(repo_url)

    upstream = _api_request("GET", f"/repos/{owner}/{repo}", github_token)
    default_branch = upstream.get("default_branch", "main")
    can_push = upstream.get("permissions", {}).get("push", False)

    user = _api_request("GET", "/user", github_token)
    user_login = user.get("login", "")

    if can_push:
        push_owner, push_repo = owner, repo
    else:
        # Check if the user already has a fork of this repository
        already_forked = False
        if user_login:
            try:
                user_repo = _api_request("GET", f"/repos/{user_login}/{repo}", github_token)
                if user_repo.get("fork"):
                    already_forked = True
                    push_owner, push_repo = user_login, repo
                    print(f"[github_pr] found existing fork at {push_owner}/{push_repo}")
            except GitHubPRError:
                pass

        if not already_forked:
            print(f"[github_pr] no push access to {owner}/{repo} -- forking...")
            try:
                fork = _api_request("POST", f"/repos/{owner}/{repo}/forks", github_token, data={})
                push_owner, push_repo = fork["owner"]["login"], fork["name"]
            except GitHubPRError as e:
                if "403" in str(e):
                    raise GitHubPRError(
                        f"GitHub API fork failed (fine-grained PATs cannot fork external repositories via API).\n"
                        f"Fix this either by:\n"
                        f"  1) Forking https://github.com/{owner}/{repo} in your browser (click 'Fork' at top right), OR\n"
                        f"  2) Using a Classic Token (ghp_...) with 'repo' scope from https://github.com/settings/tokens."
                    ) from None
                raise
            for attempt in range(FORK_POLL_ATTEMPTS):
                try:
                    _api_request("GET", f"/repos/{push_owner}/{push_repo}", github_token)
                    break
                except GitHubPRError:
                    if attempt == FORK_POLL_ATTEMPTS - 1:
                        raise
                    time.sleep(FORK_POLL_DELAY_SECONDS)

    short_sha = _run_git(repo_path, ["rev-parse", "--short", "HEAD"], github_token).stdout.strip()
    branch_name = f"fix/{issue_id}-{short_sha}"

    _run_git(repo_path, ["checkout", "-b", branch_name], github_token)
    push_url = f"https://x-access-token:{github_token}@github.com/{push_owner}/{push_repo}.git"
    _run_git(repo_path, ["remote", "add", "sutra-pr-push", push_url], github_token)
    try:
        _run_git(repo_path, ["push", "-u", "sutra-pr-push", branch_name], github_token)
    finally:
        # Remove the token-bearing remote immediately regardless of outcome --
        # the sandbox dir is disposable, but no reason to leave a credential
        # sitting in .git/config longer than the single push needs it.
        subprocess.run(["git", "remote", "remove", "sutra-pr-push"], cwd=repo_path, capture_output=True)

    issue_number = _extract_issue_number(issue_text, issue_id)
    title = f"Fix: {issue_text.strip().splitlines()[0][:72]}" if issue_text.strip() else f"Fix for {issue_id}"
    body = _compose_pr_body(issue_text, issue_number, result)

    pr = _api_request(
        "POST",
        f"/repos/{owner}/{repo}/pulls",
        github_token,
        data={
            "title": title,
            "body": body,
            "head": f"{push_owner}:{branch_name}",
            "base": default_branch,
        },
    )
    return pr["html_url"]
