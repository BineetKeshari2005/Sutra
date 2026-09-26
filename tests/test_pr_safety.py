"""Proves --create-pr cannot fire against an unintended repo. Every case here
must never touch a real GitHub repo: the two refusal paths return before any
network call, and the "proceeds" path mocks the GitHub API and git entirely.
"""
from __future__ import annotations

from unittest.mock import patch

from harness.integrations.github_pr import check_pr_authorization, create_pull_request
from harness.memory.trajectory_store import TrajectoryStore


def test_non_allowlisted_target_is_refused():
    authorized, reason = check_pr_authorization(
        repo_url="https://github.com/someorg/somerepo",
        allowed_target="https://github.com/otherorg/otherrepo",
        confirmed=True,
    )
    assert authorized is False
    assert "does not match" in reason


def test_missing_allowlist_is_refused_even_when_confirmed():
    authorized, reason = check_pr_authorization(
        repo_url="https://github.com/someorg/somerepo",
        allowed_target=None,
        confirmed=True,
    )
    assert authorized is False
    assert "no --allow-pr-target" in reason


def test_allowlisted_but_unconfirmed_is_refused():
    authorized, reason = check_pr_authorization(
        repo_url="https://github.com/someorg/somerepo",
        allowed_target="https://github.com/someorg/somerepo",
        confirmed=False,
    )
    assert authorized is False
    assert "confirm-pr" in reason


def test_allowlisted_and_confirmed_authorizes():
    authorized, reason = check_pr_authorization(
        repo_url="https://github.com/someorg/somerepo",
        allowed_target="https://github.com/someorg/somerepo",
        confirmed=True,
    )
    assert authorized is True


def test_allowlist_match_is_case_and_dotgit_insensitive():
    authorized, _ = check_pr_authorization(
        repo_url="https://github.com/SomeOrg/SomeRepo.git",
        allowed_target="https://github.com/someorg/somerepo",
        confirmed=True,
    )
    assert authorized is True


def _make_result():
    from types import SimpleNamespace

    return SimpleNamespace(
        diff="diff --git a/a.py b/a.py\n",
        gates={"patch_applies": {"passed": True, "detail": "ok"}},
        adversarial_review={"verdict": "no_concerns", "notes": "fine"},
        triage_justification="root cause",
    )


def test_create_pull_request_refuses_non_allowlisted_target_with_no_api_call(tmp_path):
    trajectory = TrajectoryStore(str(tmp_path / "t.jsonl"))
    with patch("harness.integrations.github_pr._api_request") as mock_api, \
         patch("harness.integrations.github_pr._run_git") as mock_git:
        try:
            create_pull_request(
                repo_path=str(tmp_path), repo_url="https://github.com/someorg/somerepo",
                issue_text="issue", issue_id="i1", result=_make_result(), github_token="tok",
                allowed_target="https://github.com/otherorg/otherrepo", confirmed=True,
                trajectory=trajectory,
            )
            assert False, "expected GitHubPRError"
        except Exception as e:
            assert "refused" in str(e)

    mock_api.assert_not_called()
    mock_git.assert_not_called()

    events = trajectory.read_all()
    attempts = [e for e in events if e["type"] == "pr_creation_attempt"]
    assert len(attempts) == 1
    assert attempts[0]["payload"]["authorized"] is False


def test_create_pull_request_refuses_allowlisted_but_unconfirmed_with_no_push(tmp_path):
    trajectory = TrajectoryStore(str(tmp_path / "t.jsonl"))
    with patch("harness.integrations.github_pr._api_request") as mock_api, \
         patch("harness.integrations.github_pr._run_git") as mock_git:
        try:
            create_pull_request(
                repo_path=str(tmp_path), repo_url="https://github.com/someorg/somerepo",
                issue_text="issue", issue_id="i1", result=_make_result(), github_token="tok",
                allowed_target="https://github.com/someorg/somerepo", confirmed=False,
                trajectory=trajectory,
            )
            assert False, "expected GitHubPRError"
        except Exception as e:
            assert "refused" in str(e)

    mock_api.assert_not_called()
    mock_git.assert_not_called()

    events = trajectory.read_all()
    attempts = [e for e in events if e["type"] == "pr_creation_attempt"]
    assert len(attempts) == 1
    assert attempts[0]["payload"]["authorized"] is False
    assert attempts[0]["payload"]["confirmed"] is False


def test_interactive_mode_authorizes_without_allow_target_or_confirm():
    # Part C: the interactive flow's two affirmative actions (menu choice +
    # token entry) substitute for --allow-pr-target/--confirm-pr -- neither
    # flag should be required when interactive=True.
    authorized, reason = check_pr_authorization(
        repo_url="https://github.com/someorg/somerepo",
        allowed_target=None,
        confirmed=False,
        interactive=True,
    )
    assert authorized is True
    assert "interactive" in reason


def test_flag_mode_still_requires_both_flags_when_interactive_not_set():
    # The substitution applies ONLY to the interactive path -- flag-based
    # calls (interactive=False, the default) must not be weakened by its mere
    # existence.
    authorized, _ = check_pr_authorization(
        repo_url="https://github.com/someorg/somerepo",
        allowed_target=None,
        confirmed=False,
    )
    assert authorized is False


def test_create_pull_request_interactive_mode_skips_allowlist_and_logs_mode(tmp_path):
    trajectory = TrajectoryStore(str(tmp_path / "t.jsonl"))

    api_responses = {
        ("GET", "/repos/someorg/somerepo"): {"default_branch": "main", "permissions": {"push": True}},
        ("GET", "/user"): {"login": "someorg"},
        ("POST", "/repos/someorg/somerepo/pulls"): {"html_url": "https://github.com/someorg/somerepo/pull/2"},
    }

    def fake_api(method, path, token, data=None):
        key = (method, path)
        if key in api_responses:
            return api_responses[key]
        raise AssertionError(f"unexpected API call: {method} {path}")

    def fake_git(repo_path, args, token):
        from types import SimpleNamespace
        if args[:2] == ["rev-parse", "--short"]:
            return SimpleNamespace(stdout="abc1234\n")
        return SimpleNamespace(stdout="")

    with patch("harness.integrations.github_pr._api_request", side_effect=fake_api), \
         patch("harness.integrations.github_pr._run_git", side_effect=fake_git), \
         patch("subprocess.run"):
        pr_url = create_pull_request(
            repo_path=str(tmp_path), repo_url="https://github.com/someorg/somerepo",
            issue_text="Issue #3: bug", issue_id="i1", result=_make_result(), github_token="tok",
            interactive=True,  # no allowed_target, no confirmed -- must still proceed
            trajectory=trajectory,
        )

    assert pr_url == "https://github.com/someorg/somerepo/pull/2"

    events = trajectory.read_all()
    attempts = [e for e in events if e["type"] == "pr_creation_attempt"]
    assert len(attempts) == 1
    assert attempts[0]["payload"]["authorized"] is True
    assert attempts[0]["payload"]["mode"] == "interactive"


def test_create_pull_request_forks_when_no_push_access(tmp_path):
    """Fork-if-needed path, mocked end-to-end -- never touches a real repo."""
    trajectory = TrajectoryStore(str(tmp_path / "t.jsonl"))

    api_responses = {
        ("GET", "/repos/upstream-org/upstream-repo"): {"default_branch": "main", "permissions": {"push": False}},
        ("GET", "/user"): {"login": "myuser"},
        ("POST", "/repos/upstream-org/upstream-repo/forks"): {"owner": {"login": "myuser"}, "name": "upstream-repo"},
        ("POST", "/repos/upstream-org/upstream-repo/pulls"): {"html_url": "https://github.com/upstream-org/upstream-repo/pull/9"},
    }
    from harness.integrations.github_pr import GitHubPRError

    call_log = []
    fork_check_calls = {"n": 0}

    def fake_api(method, path, token, data=None):
        call_log.append((method, path))
        key = (method, path)
        if key == ("GET", "/repos/myuser/upstream-repo"):
            fork_check_calls["n"] += 1
            if fork_check_calls["n"] == 1:
                raise GitHubPRError("404 not found")  # pre-check: no existing fork yet
            return {"fork": True}  # post-fork readiness poll: now it exists
        if key in api_responses:
            return api_responses[key]
        raise AssertionError(f"unexpected API call: {method} {path}")

    def fake_git(repo_path, args, token):
        from types import SimpleNamespace
        if args[:2] == ["rev-parse", "--short"]:
            return SimpleNamespace(stdout="abc1234\n")
        return SimpleNamespace(stdout="")

    with patch("harness.integrations.github_pr._api_request", side_effect=fake_api), \
         patch("harness.integrations.github_pr._run_git", side_effect=fake_git), \
         patch("harness.integrations.github_pr.time.sleep"), \
         patch("subprocess.run"):
        pr_url = create_pull_request(
            repo_path=str(tmp_path), repo_url="https://github.com/upstream-org/upstream-repo",
            issue_text="Issue #1: bug", issue_id="i1", result=_make_result(), github_token="tok",
            interactive=True, trajectory=trajectory,
        )

    assert pr_url == "https://github.com/upstream-org/upstream-repo/pull/9"
    assert ("POST", "/repos/upstream-org/upstream-repo/forks") in call_log


def test_create_pull_request_reuses_existing_fork_without_forking_again(tmp_path):
    trajectory = TrajectoryStore(str(tmp_path / "t.jsonl"))

    api_responses = {
        ("GET", "/repos/upstream-org/upstream-repo"): {"default_branch": "main", "permissions": {"push": False}},
        ("GET", "/user"): {"login": "myuser"},
        ("GET", "/repos/myuser/upstream-repo"): {"fork": True},  # already forked
        ("POST", "/repos/upstream-org/upstream-repo/pulls"): {"html_url": "https://github.com/upstream-org/upstream-repo/pull/10"},
    }

    call_log = []

    def fake_api(method, path, token, data=None):
        call_log.append((method, path))
        if (method, path) in api_responses:
            return api_responses[(method, path)]
        raise AssertionError(f"unexpected API call: {method} {path}")

    def fake_git(repo_path, args, token):
        from types import SimpleNamespace
        if args[:2] == ["rev-parse", "--short"]:
            return SimpleNamespace(stdout="abc1234\n")
        return SimpleNamespace(stdout="")

    with patch("harness.integrations.github_pr._api_request", side_effect=fake_api), \
         patch("harness.integrations.github_pr._run_git", side_effect=fake_git), \
         patch("subprocess.run"):
        pr_url = create_pull_request(
            repo_path=str(tmp_path), repo_url="https://github.com/upstream-org/upstream-repo",
            issue_text="Issue #1: bug", issue_id="i1", result=_make_result(), github_token="tok",
            interactive=True, trajectory=trajectory,
        )

    assert pr_url == "https://github.com/upstream-org/upstream-repo/pull/10"
    assert ("POST", "/repos/upstream-org/upstream-repo/forks") not in call_log


def test_create_pull_request_proceeds_only_when_allowlisted_and_confirmed(tmp_path):
    trajectory = TrajectoryStore(str(tmp_path / "t.jsonl"))

    api_responses = {
        ("GET", "/repos/someorg/somerepo"): {"default_branch": "main", "permissions": {"push": True}},
        ("GET", "/user"): {"login": "someorg"},
        ("POST", "/repos/someorg/somerepo/pulls"): {"html_url": "https://github.com/someorg/somerepo/pull/1"},
    }

    def fake_api(method, path, token, data=None):
        key = (method, path)
        if key in api_responses:
            return api_responses[key]
        raise AssertionError(f"unexpected API call: {method} {path}")

    def fake_git(repo_path, args, token):
        from types import SimpleNamespace
        if args[:2] == ["rev-parse", "--short"]:
            return SimpleNamespace(stdout="abc1234\n")
        return SimpleNamespace(stdout="")

    with patch("harness.integrations.github_pr._api_request", side_effect=fake_api) as mock_api, \
         patch("harness.integrations.github_pr._run_git", side_effect=fake_git) as mock_git, \
         patch("subprocess.run"):
        pr_url = create_pull_request(
            repo_path=str(tmp_path), repo_url="https://github.com/someorg/somerepo",
            issue_text="Issue #3: bug", issue_id="i1", result=_make_result(), github_token="tok",
            allowed_target="https://github.com/someorg/somerepo", confirmed=True,
            trajectory=trajectory,
        )

    assert pr_url == "https://github.com/someorg/somerepo/pull/1"
    assert mock_git.call_count >= 1  # checkout -b + remote add + push actually attempted

    events = trajectory.read_all()
    attempts = [e for e in events if e["type"] == "pr_creation_attempt"]
    assert len(attempts) == 1
    assert attempts[0]["payload"]["authorized"] is True
