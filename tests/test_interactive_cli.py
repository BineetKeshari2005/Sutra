"""Covers Part A/B/C of the interactive CLI: file-path-vs-text detection and
the post-run menu's state transitions. Menu tests mock input()/print() and
the PR/diff-showing functions -- no real subprocess, network, or terminal
interaction, matching the isolation the rest of the suite uses.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo"))

import run_demo  # noqa: E402


def test_detect_issue_text_reads_existing_file_path(tmp_path):
    issue_file = tmp_path / "issue.txt"
    issue_file.write_text("Issue #7: search state is lost on back navigation\n")
    result = run_demo._detect_issue_text(str(issue_file))
    assert result == "Issue #7: search state is lost on back navigation"


def test_detect_issue_text_treats_nonexistent_path_as_raw_text():
    raw = "search state is lost on back navigation, no file with this name exists"
    result = run_demo._detect_issue_text(raw)
    assert result == raw


def test_detect_issue_text_strips_raw_text():
    result = run_demo._detect_issue_text("  some pasted issue text  \n")
    assert result == "some pasted issue text"


def test_parse_github_issue_ref_full_url():
    ref = run_demo._parse_github_issue_ref("https://github.com/someorg/somerepo/issues/6", "")
    assert ref == ("someorg", "somerepo", 6)


def test_parse_github_issue_ref_bare_number_resolves_against_repo_url():
    ref = run_demo._parse_github_issue_ref("6", "https://github.com/someorg/somerepo")
    assert ref == ("someorg", "somerepo", 6)


def test_parse_github_issue_ref_hash_number_resolves_against_repo_url():
    ref = run_demo._parse_github_issue_ref("#6", "https://github.com/someorg/somerepo")
    assert ref == ("someorg", "somerepo", 6)


def test_parse_github_issue_ref_bare_number_without_repo_url_is_none():
    assert run_demo._parse_github_issue_ref("6", "") is None


def test_parse_github_issue_ref_plain_text_is_none():
    assert run_demo._parse_github_issue_ref("the button is broken", "https://github.com/o/r") is None


def test_detect_issue_text_fetches_full_url(monkeypatch):
    monkeypatch.setattr(
        run_demo, "_fetch_github_issue_text",
        lambda owner, repo, number: f"fetched: {owner}/{repo}#{number}",
    )
    result = run_demo._detect_issue_text("https://github.com/someorg/somerepo/issues/6", "")
    assert result == "fetched: someorg/somerepo#6"


def test_detect_issue_text_fetches_bare_number_against_given_repo(monkeypatch):
    monkeypatch.setattr(
        run_demo, "_fetch_github_issue_text",
        lambda owner, repo, number: f"fetched: {owner}/{repo}#{number}",
    )
    result = run_demo._detect_issue_text("6", "https://github.com/someorg/somerepo")
    assert result == "fetched: someorg/somerepo#6"


def test_fetch_github_issue_text_combines_title_and_body(monkeypatch):
    import json
    from io import BytesIO

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"title": "Search lost on back nav", "body": "Steps to reproduce..."}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=15: FakeResponse())
    result = run_demo._fetch_github_issue_text("someorg", "somerepo", 6)
    assert result == "Search lost on back nav\n\nSteps to reproduce..."


def test_fetch_github_issue_text_raises_clean_error_on_http_failure(monkeypatch):
    import urllib.error

    def raise_404(req, timeout=15):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", raise_404)
    try:
        run_demo._fetch_github_issue_text("someorg", "somerepo", 999)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "404" in str(e)
        assert "999" in str(e)


def _fake_result(verified=True, status="verified"):
    from types import SimpleNamespace

    return SimpleNamespace(
        status=status,
        verified=verified,
        diff="diff --git a/a.py b/a.py\n",
        gates={"patch_applies": {"passed": True, "detail": "ok"}},
        adversarial_review={"verdict": "no_concerns", "notes": "fine"} if verified else None,
        confidence_report=None if verified else {"root_cause_confidence": "low", "root_cause_summary": "x", "unresolved_issue": "y"},
        best_checkpoint=None if verified else "abc123",
        triage_justification="root cause",
        retries_used=0,
        total_tokens=100,
        total_tool_calls=2,
    )


def test_post_run_menu_review_then_end_never_touches_pr(tmp_path):
    result = _fake_result()
    with patch("builtins.input", side_effect=["1", "2"]), \
         patch("run_demo._create_pr_flow_interactive") as mock_pr:
        exit_code = run_demo._post_run_menu(str(tmp_path), "https://github.com/x/y", "issue text", "i1", result, trajectory=None)

    assert exit_code == 0
    mock_pr.assert_not_called()


def test_post_run_menu_review_then_create_pr_succeeds_and_ends_immediately(tmp_path):
    # A successful PR creation is a terminal action: no repeated "what would
    # you like to do?" menu afterward, just a clean end.
    result = _fake_result()
    with patch("builtins.input", side_effect=["1", "1"]), \
         patch("run_demo._create_pr_flow_interactive", return_value=True) as mock_pr:
        exit_code = run_demo._post_run_menu(str(tmp_path), "https://github.com/x/y", "issue text", "i1", result, trajectory=None)

    assert exit_code == 0
    mock_pr.assert_called_once()


def test_post_run_menu_review_then_create_pr_fails_and_loops_back(tmp_path):
    # A failed/cancelled PR attempt is NOT terminal -- the menu comes back so
    # the user can retry or end.
    result = _fake_result()
    with patch("builtins.input", side_effect=["1", "1", "3"]), \
         patch("run_demo._create_pr_flow_interactive", return_value=False) as mock_pr:
        exit_code = run_demo._post_run_menu(str(tmp_path), "https://github.com/x/y", "issue text", "i1", result, trajectory=None)

    assert exit_code == 0
    mock_pr.assert_called_once()


def test_post_run_menu_create_pr_directly_succeeds_and_ends_immediately(tmp_path):
    result = _fake_result()
    with patch("builtins.input", side_effect=["2"]), \
         patch("run_demo._create_pr_flow_interactive", return_value=True) as mock_pr:
        exit_code = run_demo._post_run_menu(str(tmp_path), "https://github.com/x/y", "issue text", "i1", result, trajectory=None)

    assert exit_code == 0
    mock_pr.assert_called_once()


def test_post_run_menu_create_pr_directly_fails_and_loops_back(tmp_path):
    # Reproduces the reported UX bug: previously, even a SUCCESSFUL PR
    # creation looped back to the full menu repeatedly. A failed one should
    # still loop back (so the user can retry), which this covers.
    result = _fake_result()
    with patch("builtins.input", side_effect=["2", "3"]), \
         patch("run_demo._create_pr_flow_interactive", return_value=False) as mock_pr:
        exit_code = run_demo._post_run_menu(str(tmp_path), "https://github.com/x/y", "issue text", "i1", result, trajectory=None)

    assert exit_code == 0
    mock_pr.assert_called_once()


def test_post_run_menu_end_directly(tmp_path):
    result = _fake_result()
    with patch("builtins.input", side_effect=["3"]), \
         patch("run_demo._create_pr_flow_interactive") as mock_pr:
        exit_code = run_demo._post_run_menu(str(tmp_path), "https://github.com/x/y", "issue text", "i1", result, trajectory=None)

    assert exit_code == 0
    mock_pr.assert_not_called()


def test_post_run_menu_reprompts_on_invalid_choice(tmp_path):
    result = _fake_result()
    with patch("builtins.input", side_effect=["9", "3"]):
        exit_code = run_demo._post_run_menu(str(tmp_path), "https://github.com/x/y", "issue text", "i1", result, trajectory=None)
    assert exit_code == 0


def test_create_pr_flow_refuses_when_not_verified(capsys):
    result = _fake_result(verified=False, status="unresolved")
    with patch("getpass.getpass") as mock_getpass:
        succeeded = run_demo._create_pr_flow_interactive("/repo", "https://github.com/x/y", "issue", "i1", result, trajectory=None)
    mock_getpass.assert_not_called()
    assert succeeded is False
    assert "not verified" in capsys.readouterr().out


def test_show_diff_and_confidence_shows_confidence_report_when_unresolved(capsys):
    result = _fake_result(verified=False, status="unresolved")
    run_demo._show_diff_and_confidence(result)
    out = capsys.readouterr().out
    assert "confidence report" in out
    assert "abc123" in out  # best_checkpoint
    assert "root cause" not in out or "root_cause_summary" in out  # field label present
    assert "root_cause_confidence: low" in out


def test_show_diff_and_confidence_omits_confidence_report_when_verified(capsys):
    result = _fake_result(verified=True, status="verified")
    run_demo._show_diff_and_confidence(result)
    out = capsys.readouterr().out
    assert "confidence report" not in out


def test_create_pr_flow_cancels_on_empty_token(capsys):
    result = _fake_result(verified=True)
    with patch("getpass.getpass", return_value=""):
        succeeded = run_demo._create_pr_flow_interactive("/repo", "https://github.com/x/y", "issue", "i1", result, trajectory=None)
    assert succeeded is False
    assert "No token entered" in capsys.readouterr().out


def test_create_pr_flow_returns_true_on_success(capsys):
    result = _fake_result(verified=True)
    with patch("getpass.getpass", return_value="ghp_faketoken"), \
         patch("harness.integrations.github_pr.create_pull_request", return_value="https://github.com/x/y/pull/1"):
        succeeded = run_demo._create_pr_flow_interactive("/repo", "https://github.com/x/y", "issue", "i1", result, trajectory=None)
    assert succeeded is True
    out = capsys.readouterr().out
    assert "PR opened successfully" in out
    assert "https://github.com/x/y/pull/1" in out


def test_create_pr_flow_returns_false_on_github_error(capsys):
    from harness.integrations.github_pr import GitHubPRError

    result = _fake_result(verified=True)
    with patch("getpass.getpass", return_value="ghp_faketoken"), \
         patch("harness.integrations.github_pr.create_pull_request", side_effect=GitHubPRError("bad credentials")):
        succeeded = run_demo._create_pr_flow_interactive("/repo", "https://github.com/x/y", "issue", "i1", result, trajectory=None)
    assert succeeded is False
    assert "bad credentials" in capsys.readouterr().out


def test_prompt_issue_returns_known_issue_number_for_full_url(monkeypatch):
    monkeypatch.setattr(
        run_demo, "_fetch_github_issue_text",
        lambda owner, repo, number: f"fetched: {owner}/{repo}#{number}",
    )
    with patch("builtins.input", return_value="https://github.com/someorg/somerepo/issues/6"):
        issue_text, issue_number = run_demo._prompt_issue("")
    assert issue_number == 6
    assert issue_text == "fetched: someorg/somerepo#6"


def test_prompt_issue_returns_known_issue_number_for_bare_number(monkeypatch):
    monkeypatch.setattr(
        run_demo, "_fetch_github_issue_text",
        lambda owner, repo, number: f"fetched: {owner}/{repo}#{number}",
    )
    with patch("builtins.input", return_value="6"):
        issue_text, issue_number = run_demo._prompt_issue("https://github.com/someorg/somerepo")
    assert issue_number == 6


def test_prompt_issue_returns_none_for_raw_text_or_file(tmp_path):
    with patch("builtins.input", return_value="the button is broken"):
        _, issue_number = run_demo._prompt_issue("")
    assert issue_number is None

    issue_file = tmp_path / "issue.txt"
    issue_file.write_text("some issue text")
    with patch("builtins.input", return_value=str(issue_file)):
        _, issue_number = run_demo._prompt_issue("")
    assert issue_number is None


def test_interactive_issue_id_embeds_known_issue_number_for_extract_issue_number():
    """The actual end-to-end point of this fix: an issue_id built from a
    known issue number must be something github_pr.py's real
    _extract_issue_number() picks up, so the PR body gets a real "Fixes #N"
    line instead of silently omitting it."""
    from harness.integrations.github_pr import _extract_issue_number

    issue_number = 6
    issue_id = f"issue-{issue_number}" if issue_number is not None else "interactive-abcd1234"
    assert issue_id == "issue-6"
    # Even with issue_text that (realistically) never repeats its own number:
    fetched_body = "Search state is lost on back navigation\n\nSteps to reproduce: ..."
    assert _extract_issue_number(fetched_body, issue_id) == 6


def test_run_orchestrator_safely_returns_result_on_success():
    class FakeOrchestrator:
        def run(self):
            return "the real result"

    assert run_demo._run_orchestrator_safely(FakeOrchestrator()) == "the real result"


def test_run_orchestrator_safely_catches_provider_failure_and_returns_none(capsys):
    class FakeOrchestrator:
        def run(self):
            raise RuntimeError("GroqException - tool_use_failed: malformed tool call")

    result = run_demo._run_orchestrator_safely(FakeOrchestrator())
    assert result is None
    out = capsys.readouterr().out
    assert "ERROR: the model provider failed mid-run" in out
    assert "tool_use_failed" in out
    assert "provider's fault, not Sutra's" in out


def test_groq_selection_prints_reliability_warning(monkeypatch, capsys):
    for env_var, _, _ in run_demo._LIVE_ADAPTER_CANDIDATES:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "dummy")

    run_demo._detect_live_adapter()

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "unreliable" in out
