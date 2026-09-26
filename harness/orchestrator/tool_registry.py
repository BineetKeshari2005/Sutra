"""Tool schemas (OpenAI function-calling format) and dispatch table shared by
every phase of the state machine."""
from __future__ import annotations

from typing import Any, Callable

from harness.tools import bash, file_ops, git_ops, search, test_runner

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search the repo for a literal string or regex pattern. Returns matching file:line hits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "description": "subdirectory to search, default '.'"},
                    "regex": {"type": "boolean", "description": "treat query as a regex, default false"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_symbol",
            "description": "Find a Python function or class definition by exact name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol_name": {"type": "string"},
                    "path": {"type": "string", "description": "subdirectory to search, default '.'"},
                },
                "required": ["symbol_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_file",
            "description": "Read a windowed slice of a file's contents with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace an exact, unique substring (old_str) in a file with new_str. "
                "old_str must match exactly (including whitespace) and occur exactly once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the repo's pytest suite (optionally scoped to test_path) and get a structured pass/fail summary.",
            "parameters": {
                "type": "object",
                "properties": {"test_path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Escape-hatch sandboxed shell command, for anything the other tools don't cover.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show the current uncommitted diff against HEAD.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def make_dispatch(repo_path: str) -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "search_code": lambda query, path=".", regex=False: search.search_code(repo_path, query, path, regex),
        "search_symbol": lambda symbol_name, path=".": search.search_symbol(repo_path, symbol_name, path),
        "open_file": lambda path, start_line=None, end_line=None: file_ops.open_file(repo_path, path, start_line, end_line),
        "edit_file": lambda path, old_str, new_str: file_ops.edit_file(repo_path, path, old_str, new_str),
        "run_tests": lambda test_path=None: test_runner.run_tests(repo_path, test_path),
        "run_bash": lambda command: bash.run_bash(repo_path, command),
        "git_diff": lambda: git_ops.git_diff(repo_path),
    }
