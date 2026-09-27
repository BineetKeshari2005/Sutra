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


def test_open_file_caps_a_single_oversized_read(tiny_repo):
    # A dense file whose 200-line default window would exceed the size cap --
    # simulates a real, sizeable TSX/JSX file, not more-itertools' terse Python.
    dense_line = "x" * 200 + "\n"  # ~200 chars/line, well over Python's typical density
    with open(os.path.join(tiny_repo, "dense.py"), "w") as f:
        f.writelines([dense_line] * 200)

    result = file_ops.open_file(tiny_repo, "dense.py")

    assert result["ok"] is True
    assert len(result["content"]) <= file_ops.MAX_CONTENT_CHARS
    assert result["end_line"] < 200  # didn't return the whole 200-line default window
    assert "size cap" in result["hint"]
    assert f"start_line={result['end_line'] + 1}" in result["hint"]


def test_open_file_under_cap_is_unaffected(tiny_repo):
    result = file_ops.open_file(tiny_repo, "a.py")
    assert result["ok"] is True
    assert "size cap" not in result.get("hint", "")


def test_git_checkpoint_commits_changes(tiny_repo):
    file_ops.edit_file(tiny_repo, "b.py", "VALUE = 42", "VALUE = 7")
    result = git_ops.git_checkpoint(tiny_repo, "test commit")
    assert result["ok"] is True
    assert result["committed"] is True

    result2 = git_ops.git_checkpoint(tiny_repo, "no-op commit")
    assert result2["committed"] is False


def test_open_file_and_edit_file_rejects_binary_file(tiny_repo):
    binary_path = os.path.join(tiny_repo, "image.png")
    with open(binary_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")

    res_open = file_ops.open_file(tiny_repo, "image.png")
    assert res_open["ok"] is False
    assert "binary file detected" in res_open["error"]

    res_edit = file_ops.edit_file(tiny_repo, "image.png", "PNG", "JPEG")
    assert res_edit["ok"] is False
    assert "binary file detected" in res_edit["error"]

