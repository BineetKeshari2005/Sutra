"""run_bash: sandboxed escape-hatch shell. Runs inside the repo sandbox dir
with a wall-clock timeout and (on POSIX) CPU-time/memory rlimits, output
truncated to keep observations small."""
from __future__ import annotations

import subprocess
import sys
from typing import Any

MAX_OUTPUT_CHARS = 4000
DEFAULT_TIMEOUT = 60
CPU_SECONDS_LIMIT = 60
MEMORY_BYTES_LIMIT = 1024 * 1024 * 1024  # 1 GiB


def _preexec_limits():
    if sys.platform == "win32":
        return None

    def _set():
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS_LIMIT, CPU_SECONDS_LIMIT))
            # RLIMIT_AS is not supported on macOS -- skip it there
            if sys.platform != "darwin":
                resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES_LIMIT, MEMORY_BYTES_LIMIT))
        except Exception:
            pass  # rlimit failures must not crash the child process setup

    return _set


def run_bash(repo_path: str, command: str, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_preexec_limits(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"command exceeded {timeout}s timeout",
            "hint": "the command may be hanging; try a narrower or non-interactive command",
        }
    except MemoryError:
        return {"ok": False, "error": "command exceeded memory limit"}
    except subprocess.SubprocessError as e:
        return {"ok": False, "error": f"subprocess error: {e}"}

    output = (proc.stdout + proc.stderr)[-MAX_OUTPUT_CHARS:]
    return {
        "ok": True,
        "returncode": proc.returncode,
        "output": output,
        "truncated": len(proc.stdout + proc.stderr) > MAX_OUTPUT_CHARS,
    }
