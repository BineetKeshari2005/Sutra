"""run_tests: invoke the target repo's own pytest suite and return truncated,
structured output -- never raw multi-thousand-line pytest logs."""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from typing import Any

MAX_OUTPUT_CHARS = 4000
SUMMARY_RE = re.compile(
    r"(?P<counts>(?:\d+ \w+,?\s*)+) in (?P<duration>[\d.]+)s"
)


def run_tests(repo_path: str, test_path: str | None = None, timeout: int = 120) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "pytest", "-q"]
    if test_path:
        cmd.extend(shlex.split(test_path))

    try:
        proc = subprocess.run(
            cmd, cwd=repo_path, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"test run exceeded {timeout}s timeout",
            "hint": "narrow test_path to the specific failing test, or the change may have introduced an infinite loop",
        }
    except FileNotFoundError:
        return {"ok": False, "error": "pytest not found in sandbox", "hint": "ensure pytest is installed in the sandbox environment"}

    output = proc.stdout + "\n" + proc.stderr
    summary = _parse_summary(output)
    truncated_output = output[-MAX_OUTPUT_CHARS:]

    return {
        "ok": True,
        "passed": proc.returncode == 0,
        "returncode": proc.returncode,
        "summary": summary,
        "output_tail": truncated_output,
        "truncated": len(output) > MAX_OUTPUT_CHARS,
    }


def _parse_summary(output: str) -> str:
    lines = [l for l in output.splitlines() if l.strip()]
    for line in reversed(lines):
        if SUMMARY_RE.search(line) or "no tests ran" in line:
            return line.strip()
    return lines[-1].strip() if lines else "(no output)"
