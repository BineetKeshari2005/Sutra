"""search_code: text search (ripgrep if present, pure-Python fallback otherwise)
plus a lightweight regex-based symbol search (def/class) for Python files."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any

MAX_MATCHES = 50
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".mypy_cache"}


def _iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def search_code(repo_path: str, query: str, path: str = ".", regex: bool = False) -> dict[str, Any]:
    """Search for `query` (literal by default, regex if regex=True) under repo_path/path."""
    search_root = os.path.join(repo_path, path)
    if not os.path.isdir(search_root):
        return {
            "ok": False,
            "error": f"search path '{path}' does not exist under repo",
            "hint": "check the path argument; use '.' to search the whole repo",
        }

    if shutil.which("rg"):
        return _search_with_ripgrep(repo_path, search_root, query, regex)
    return _search_pure_python(search_root, query, regex)


def _search_with_ripgrep(repo_path: str, search_root: str, query: str, regex: bool) -> dict[str, Any]:
    cmd = ["rg", "--line-number", "--no-heading", "--max-count", "5"]
    if not regex:
        cmd.append("--fixed-strings")
    cmd += [query, search_root]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "search timed out after 20s", "hint": "narrow the search path or query"}
    matches = []
    for line in proc.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        file_abs, line_no, text = parts
        matches.append({"file": os.path.relpath(file_abs, repo_path), "line": int(line_no), "text": text.strip()})
    return _finalize(matches, query)


def _search_pure_python(search_root: str, query: str, regex: bool) -> dict[str, Any]:
    pattern = re.compile(query) if regex else None
    matches = []
    repo_root = search_root
    for fp in _iter_files(search_root):
        if not fp.endswith((".py", ".md", ".rst", ".txt", ".toml", ".cfg", ".ini", ".yml", ".yaml")):
            continue
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    hit = pattern.search(line) if pattern else (query in line)
                    if hit:
                        matches.append({"file": os.path.relpath(fp, repo_root), "line": i, "text": line.strip()})
                        if len(matches) >= MAX_MATCHES + 1:
                            break
        except OSError:
            continue
        if len(matches) >= MAX_MATCHES + 1:
            break
    return _finalize(matches, query)


def _finalize(matches: list[dict], query: str) -> dict[str, Any]:
    truncated = len(matches) > MAX_MATCHES
    matches = matches[:MAX_MATCHES]
    if not matches:
        return {
            "ok": True,
            "matches": [],
            "hint": f"no matches for '{query}'; try a shorter substring or check spelling/case",
        }
    result: dict[str, Any] = {"ok": True, "matches": matches, "count": len(matches)}
    if truncated:
        result["hint"] = f"results truncated at {MAX_MATCHES}; narrow the query or path to see more"
    return result


_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(")
_CLASS_RE = re.compile(r"^\s*class\s+(\w+)\s*[:(]")


def search_symbol(repo_path: str, symbol_name: str, path: str = ".") -> dict[str, Any]:
    """Find Python def/class declarations matching `symbol_name` (exact name match)."""
    search_root = os.path.join(repo_path, path)
    if not os.path.isdir(search_root):
        return {"ok": False, "error": f"search path '{path}' does not exist under repo"}

    hits = []
    for fp in _iter_files(search_root):
        if not fp.endswith(".py"):
            continue
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    m = _DEF_RE.match(line) or _CLASS_RE.match(line)
                    if m and m.group(1) == symbol_name:
                        hits.append({"file": os.path.relpath(fp, repo_path), "line": i, "text": line.strip()})
        except OSError:
            continue

    if not hits:
        return {
            "ok": True,
            "matches": [],
            "hint": f"no def/class named '{symbol_name}' found; try search_code for partial/substring matches",
        }
    return {"ok": True, "matches": hits, "count": len(hits)}
