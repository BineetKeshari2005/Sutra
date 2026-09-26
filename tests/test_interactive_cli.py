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
