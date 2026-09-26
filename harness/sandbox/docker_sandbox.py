"""Docker-per-run sandbox. Not a hard dependency: get_sandbox() below falls
back to LocalSandbox whenever the Docker daemon isn't reachable, which is the
case in this dev environment (docker CLI present, daemon not running)."""
from __future__ import annotations

import shutil
import subprocess


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


class DockerSandbox:
    """Placeholder for a container-per-run sandbox (image build + `docker exec`
    for tool commands). Not implemented in Phase 0 since the demo environment
    has no running daemon -- get_sandbox() below routes to LocalSandbox instead.
    Left as a distinct class so swapping in a real implementation later doesn't
    touch orchestrator code."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("DockerSandbox is not implemented; docker daemon unavailable in this environment")


def get_sandbox(work_root: str | None = None):
    from .local_sandbox import LocalSandbox

    if docker_available():
        try:
            return DockerSandbox()
        except NotImplementedError:
            pass
    return LocalSandbox(work_root=work_root)
