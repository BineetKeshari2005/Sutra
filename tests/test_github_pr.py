from types import SimpleNamespace

import pytest

from harness.integrations.github_pr import (
    GitHubPRError,
    _changed_files,
    _compose_pr_body,
    _extract_issue_number,
    _parse_owner_repo,
    _redact,
)


def test_parse_owner_repo_handles_various_url_forms():
    assert _parse_owner_repo("https://github.com/foo/bar") == ("foo", "bar")
    assert _parse_owner_repo("https://github.com/foo/bar.git") == ("foo", "bar")
    assert _parse_owner_repo("github.com/foo/bar") == ("foo", "bar")


def test_parse_owner_repo_rejects_bad_url():
    with pytest.raises(GitHubPRError):
        _parse_owner_repo("https://github.com/")


def test_extract_issue_number_prefers_hash_in_issue_text():
    assert _extract_issue_number("Issue #7: search lost on nav", "some-file") == 7


def test_extract_issue_number_falls_back_to_filename():
    assert _extract_issue_number("no hash number here", "cineverse-issue-7") == 7


def test_extract_issue_number_returns_none_when_absent():
    assert _extract_issue_number("nothing to find", "my-issue") is None


def test_changed_files_parses_unified_diff_headers():
    diff = (
        "diff --git a/src/a.py b/src/a.py\nindex 1..2 100644\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1 @@\n-x\n+y\n"
        "diff --git a/src/b.py b/src/b.py\nindex 3..4 100644\n"
    )
    assert _changed_files(diff) == ["src/a.py", "src/b.py"]


def test_changed_files_empty_diff_returns_empty_list():
    assert _changed_files("") == []


def test_redact_hides_token_in_arbitrary_text():
    assert _redact("push failed: https://x-access-token:SECRET123@github.com/x/y.git", "SECRET123") == (
        "push failed: https://x-access-token:***@github.com/x/y.git"
    )


def test_redact_no_token_is_a_no_op():
    assert _redact("some message", "") == "some message"


def test_compose_pr_body_includes_fixes_line_gates_and_review():
    result = SimpleNamespace(
        diff="diff --git a/a.tsx b/a.tsx\n",
        gates={"patch_applies": {"passed": True, "detail": "3 lines"}, "builds": {"passed": False, "detail": "type error"}},
        adversarial_review={"verdict": "minor_concerns", "notes": "check the edge case"},
        triage_justification="Root cause is a stale useState.",
    )
    body = _compose_pr_body("Issue #7: bug", 7, result)

    assert body.startswith("Fixes #7")
    assert "Root cause is a stale useState." in body
    assert "`a.tsx`" in body
    assert "[x] `patch_applies`" in body
    assert "[ ] `builds`" in body
    assert "minor_concerns" in body
    assert "Generated autonomously by" in body


def test_compose_pr_body_omits_fixes_line_when_no_issue_number():
    result = SimpleNamespace(diff="", gates={}, adversarial_review=None, triage_justification="fix")
    body = _compose_pr_body("no number here", None, result)
    assert not body.startswith("Fixes #")
