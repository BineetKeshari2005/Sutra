"""open_file (windowed read) and edit_file (diff-based, exact-match replace).

edit_file never guesses: if old_str is missing or ambiguous, it returns a
structured error observation that tells the model exactly how to fix its
next attempt, rather than applying a best-effort edit.
"""
from __future__ import annotations

import os
from typing import Any

DEFAULT_WINDOW = 200


def _resolve(repo_path: str, path: str) -> str | None:
    full = os.path.normpath(os.path.join(repo_path, path))
    if not full.startswith(os.path.normpath(repo_path)):
        return None  # path escapes the sandboxed repo
    return full


def open_file(
    repo_path: str, path: str, start_line: int | None = None, end_line: int | None = None
) -> dict[str, Any]:
    full = _resolve(repo_path, path)
    if full is None:
        return {"ok": False, "error": "path escapes the repository sandbox", "hint": "use a path relative to the repo root"}
    if not os.path.isfile(full):
        return {"ok": False, "error": f"file not found: {path}", "hint": "use search_code to locate the correct file path"}

    with open(full, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    total = len(lines)

    if start_line is None:
        start_line = 1
    if end_line is None:
        end_line = min(total, start_line + DEFAULT_WINDOW - 1)
    start_idx = max(0, start_line - 1)
    end_idx = min(total, end_line)

    if start_idx >= total:
        return {
            "ok": False,
            "error": f"start_line {start_line} is past end of file ({total} lines)",
            "hint": f"file has {total} lines; request a smaller start_line",
        }

    window = lines[start_idx:end_idx]
    numbered = "".join(f"{i + start_idx + 1:>6}\t{line}" for i, line in enumerate(window))
    result: dict[str, Any] = {
        "ok": True,
        "path": path,
        "total_lines": total,
        "start_line": start_idx + 1,
        "end_line": end_idx,
        "content": numbered,
    }
    if end_idx < total:
        result["hint"] = f"showing lines {start_idx + 1}-{end_idx} of {total}; pass start_line={end_idx + 1} to continue"
    return result


def edit_file(repo_path: str, path: str, old_str: str, new_str: str) -> dict[str, Any]:
    full = _resolve(repo_path, path)
    if full is None:
        return {"ok": False, "error": "path escapes the repository sandbox", "hint": "use a path relative to the repo root"}
    if not os.path.isfile(full):
        return {"ok": False, "error": f"file not found: {path}", "hint": "use search_code to locate the correct file path"}

    with open(full, encoding="utf-8", errors="ignore") as f:
        content = f.read()

    count = content.count(old_str)
    if count == 0:
        return {
            "ok": False,
            "error": "old_str not found in file",
            "hint": (
                "the exact text (including whitespace/indentation) was not found; "
                "re-open the file to copy the exact current text -- never guess"
            ),
        }
    if count > 1:
        return {
            "ok": False,
            "error": f"old_str is not unique in file ({count} occurrences)",
            "hint": "add more surrounding context to old_str so it uniquely identifies one location -- never guess which one",
        }

    new_content = content.replace(old_str, new_str, 1)
    with open(full, "w", encoding="utf-8") as f:
        f.write(new_content)

    return {
        "ok": True,
        "path": path,
        "lines_removed": old_str.count("\n") + 1,
        "lines_added": new_str.count("\n") + 1,
    }
