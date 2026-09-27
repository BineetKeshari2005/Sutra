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


def test_post_run_menu_review_then_create_pr(tmp_path):
    # After "Review code" -> "Create PR", the flow loops back to the
    # top-level menu (it's not a terminal action), so a final choice is
    # still needed to end the session.
    result = _fake_result()
    with patch("builtins.input", side_effect=["1", "1", "3"]), \
         patch("run_demo._create_pr_flow_interactive") as mock_pr:
        exit_code = run_demo._post_run_menu(str(tmp_path), "https://github.com/x/y", "issue text", "i1", result, trajectory=None)

    assert exit_code == 0
    mock_pr.assert_called_once()


def test_post_run_menu_create_pr_directly_from_top_level(tmp_path):
    result = _fake_result()
    with patch("builtins.input", side_effect=["2", "3"]), \
         patch("run_demo._create_pr_flow_interactive") as mock_pr:
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
        run_demo._create_pr_flow_interactive("/repo", "https://github.com/x/y", "issue", "i1", result, trajectory=None)
    mock_getpass.assert_not_called()
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
        run_demo._create_pr_flow_interactive("/repo", "https://github.com/x/y", "issue", "i1", result, trajectory=None)
    assert "No token entered" in capsys.readouterr().out
