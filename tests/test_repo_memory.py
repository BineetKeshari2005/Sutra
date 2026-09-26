import os

from harness.memory.repo_memory import RepoMemory


def test_entries_persist_across_instances(tmp_path):
    store_dir = str(tmp_path / "store")
    mem = RepoMemory("repo-x", store_dir=store_dir)
    mem.add_entries("landmines", [{"path": "a.py", "symbol": "foo", "note": "foo is fragile"}], learned_from_issue="issue-1")

    assert os.path.isfile(mem.path)

    reloaded = RepoMemory("repo-x", store_dir=store_dir)
    assert len(reloaded.data["landmines"]) == 1
    assert reloaded.data["landmines"][0]["learned_from_issue"] == "issue-1"


def test_query_relevant_surfaces_landmines_always_and_filters_others_by_overlap(tmp_path):
    mem = RepoMemory("repo-x", store_dir=str(tmp_path / "store"))
    mem.add_entries("landmines", [{"path": "a.py", "note": "editing foo() alone breaks bar()"}], learned_from_issue="i1")
    mem.add_entries("conventions", [{"note": "tests live in tests/test_widgets.py"}], learned_from_issue="i1")
    mem.add_entries("conventions", [{"note": "completely unrelated database migration guidance"}], learned_from_issue="i1")

    results = mem.query_relevant("bug in foo() breaks something in bar()")
    categories = {r["category"] for r in results}

    assert "landmines" in categories  # always surfaced
    notes = [r["note"] for r in results]
    assert "completely unrelated database migration guidance" not in notes


def test_symbol_index_cache_hit_on_unchanged_file_miss_on_changed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    file_path = repo / "mod.py"
    file_path.write_text("def foo():\n    pass\n\n\ndef bar():\n    pass\n")

    mem = RepoMemory("repo-x", store_dir=str(tmp_path / "store"))
    pristine = file_path.read_text()
    index = mem.update_symbol_index("mod.py", pristine)
    assert index == {"foo": 1, "bar": 5}

    # unchanged file -> fresh cache hit
    hit = mem.get_fresh_symbol_index(str(repo), "mod.py")
    assert hit == {"foo": 1, "bar": 5}

    # file changes -> fingerprint mismatch -> cache miss
    file_path.write_text("def foo():\n    pass\n\n\ndef bar():\n    pass\n\n\ndef baz():\n    pass\n")
    miss = mem.get_fresh_symbol_index(str(repo), "mod.py")
    assert miss is None


def test_get_fresh_symbol_index_returns_none_when_nothing_cached(tmp_path):
    mem = RepoMemory("repo-x", store_dir=str(tmp_path / "store"))
    assert mem.get_fresh_symbol_index(str(tmp_path), "nope.py") is None
