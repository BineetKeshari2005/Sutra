import os
import subprocess

import pytest

from harness.tools import file_ops, git_ops, search


@pytest.fixture
def tiny_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def foo():\n    return 1\n\ndef foo():\n    return 2\n")
    (repo / "b.py").write_text("VALUE = 42\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return str(repo)


def test_edit_file_not_found_gives_hint(tiny_repo):
    result = file_ops.edit_file(tiny_repo, "b.py", "MISSING = 1", "X = 2")
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert "hint" in result


def test_edit_file_ambiguous_gives_hint(tiny_repo):
    result = file_ops.edit_file(tiny_repo, "a.py", "return 1", "return 99")
    assert result["ok"] is True  # "return 1" only occurs once
    result2 = file_ops.edit_file(tiny_repo, "a.py", "def foo():", "def bar():")
    assert result2["ok"] is False
    assert "not unique" in result2["error"]
    assert "never guess" in result2["hint"]


def test_edit_file_applies_unique_match(tiny_repo):
    result = file_ops.edit_file(tiny_repo, "b.py", "VALUE = 42", "VALUE = 99")
    assert result["ok"] is True
    with open(os.path.join(tiny_repo, "b.py")) as f:
        assert "VALUE = 99" in f.read()


def test_search_code_finds_match(tiny_repo):
    result = search.search_code(tiny_repo, "VALUE")
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"][0]["file"] == "b.py"


def test_search_code_no_match_gives_hint(tiny_repo):
    result = search.search_code(tiny_repo, "NOPE_NOT_HERE")
    assert result["ok"] is True
    assert result["matches"] == []
    assert "hint" in result


def test_git_checkpoint_commits_changes(tiny_repo):
    file_ops.edit_file(tiny_repo, "b.py", "VALUE = 42", "VALUE = 7")
    result = git_ops.git_checkpoint(tiny_repo, "test commit")
    assert result["ok"] is True
    assert result["committed"] is True

    result2 = git_ops.git_checkpoint(tiny_repo, "no-op commit")
    assert result2["committed"] is False
