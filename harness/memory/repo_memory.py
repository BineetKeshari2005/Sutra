"""Persistent per-repo institutional memory: a single JSON file per repo,
keyed by a stable repo identifier -- the repo's root commit hash, which never
changes even though the demo sandbox re-clones and re-commits test
scaffolding on every single run. SQLite would be overkill for a handful of
short natural-language notes; a JSON file is plenty.

Every entry is meant to read like a note from a colleague (a sentence, not a
structured taxonomy), because it gets re-injected into the model's context
later -- a wall of structured junk would just burn future runs' budget for
no benefit.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from typing import Any

STORE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "store")

_ENTRY_CATEGORIES = ("conventions", "landmines", "fix_patterns")

_DEF_RE = re.compile(r"^(?:def|class)\s+(\w+)", re.MULTILINE)

_STOPWORDS = {
    "this", "that", "with", "from", "have", "will", "when", "which", "should",
    "would", "there", "their", "issue", "instead", "raise", "raised", "raises",
    "given", "using", "value", "returns", "return", "these", "those", "about",
}


def repo_id_for(repo_path: str) -> str:
    """The repo's root commit hash, short-hashed. Stable across every ephemeral
    sandbox clone of "the same repo", since the very first commit in a repo's
    history never changes -- unlike whatever test-scaffolding commit a given
    demo run happens to check out or create on top of it."""
    proc = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True
    )
    root_shas = proc.stdout.strip().splitlines()
    root = root_shas[0] if root_shas else "unknown"
    return hashlib.sha256(root.encode()).hexdigest()[:16]


def _empty_schema(repo_id: str) -> dict[str, Any]:
    return {
        "repo_id": repo_id,
        "conventions": [],
        "landmines": [],
        "fix_patterns": [],
        "symbol_index_cache": {},
        "last_updated": None,
    }


def _fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()


def _significant_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z_]{4,}", text.lower())} - _STOPWORDS


class RepoMemory:
    def __init__(self, repo_id: str, store_dir: str = STORE_DIR):
        self.repo_id = repo_id
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)
        self.path = os.path.join(store_dir, f"{repo_id}.json")
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if os.path.isfile(self.path):
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        return _empty_schema(self.repo_id)

    def save(self) -> None:
        self.data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    # ---- write path -----------------------------------------------------

    def add_entries(self, category: str, entries: list[dict[str, Any]], learned_from_issue: str) -> list[dict]:
        if category not in _ENTRY_CATEGORIES or not entries:
            return []
        added = []
        for e in entries:
            record = {**e, "learned_from_issue": learned_from_issue}
            self.data.setdefault(category, []).append(record)
            added.append(record)
        if added:
            self.save()
        return added

    def update_symbol_index(self, path: str, pristine_content: str) -> dict[str, int]:
        """Build a top-level def/class -> line-number index for the whole file
        (not just the symbol this run happened to touch) from its PRISTINE
        content (as of the run's base commit, before any agent edits) -- this
        is what makes reuse on a later, different-symbol issue legitimate
        rather than coincidental."""
        index = {m.group(1): pristine_content.count("\n", 0, m.start()) + 1 for m in _DEF_RE.finditer(pristine_content)}
        self.data.setdefault("symbol_index_cache", {})[path] = {
            "fingerprint": _fingerprint(pristine_content),
            "index": index,
        }
        self.save()
        return index

    # ---- read path ------------------------------------------------------

    def query_relevant(self, issue_text: str, max_entries: int = 5) -> list[dict[str, Any]]:
        """Simple keyword/path overlap relevance -- no embeddings. Landmines
        are always surfaced (cheap, high-value); conventions/fix_patterns need
        at least one word of overlap with the issue text."""
        issue_words = _significant_words(issue_text)
        scored: list[tuple[int, str, dict]] = []
        for category in _ENTRY_CATEGORIES:
            for entry in self.data.get(category, []):
                note = entry.get("note") or entry.get("pattern") or ""
                entry_words = _significant_words(note) | _significant_words(entry.get("path", ""))
                overlap = len(issue_words & entry_words)
                if overlap > 0 or category == "landmines":
                    scored.append((overlap, category, entry))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [{"category": c, **e} for _, c, e in scored[:max_entries]]

    def get_fresh_symbol_index(self, repo_path: str, path: str) -> dict[str, int] | None:
        """Returns the cached index for `path` iff the file's current on-disk
        content (pre-edit, since this is only ever called at the start of
        Localize) still matches the fingerprint the cache was built from."""
        cached = self.data.get("symbol_index_cache", {}).get(path)
        if not cached:
            return None
        full = os.path.join(repo_path, path)
        if not os.path.isfile(full):
            return None
        with open(full, encoding="utf-8", errors="ignore") as f:
            content = f.read()
        if _fingerprint(content) != cached.get("fingerprint"):
            return None
        return cached.get("index")

    def all_fresh_symbol_indexes(self, repo_path: str) -> dict[str, dict[str, int]]:
        hits = {}
        for path in list(self.data.get("symbol_index_cache", {})):
            index = self.get_fresh_symbol_index(repo_path, path)
            if index is not None:
                hits[path] = index
        return hits
