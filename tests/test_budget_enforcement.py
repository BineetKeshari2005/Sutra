import subprocess

from harness.memory.repo_memory import RepoMemory, repo_id_for
from harness.memory.trajectory_store import TrajectoryStore
from harness.model_adapter.mock_adapter import MockAdapter
from harness.orchestrator import budget as budget_mod
from harness.orchestrator.state_machine import Orchestrator, RunConfig


def _make_orchestrator(adapter, config, trajectory, tmp_path, **kwargs):
    # Use a throwaway memory store dir per test instead of the real
    # harness/memory/store/, so test repos never pollute committed memory state.
    memory = RepoMemory(repo_id_for(config.repo_path), store_dir=str(tmp_path / "memstore"))
    return Orchestrator(adapter, config, trajectory, repo_memory=memory, **kwargs)


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b  # bug: should be +\n")
    (repo / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    return str(repo), base


def _oversized_script(n_attempts: int = 10):
    """A script with far more tool-bearing steps than a starved budget can
    afford, so if enforcement is real, the loop must be cut off before it
    finishes them all. Content-only header steps are deliberately generic
    (rather than phase-specific prose) since naive-baseline mode skips the
    triage adapter call entirely, shifting every later step by one slot --
    the test only cares about tool-call cutoff behavior, not phase narrative."""
    script = [
        {"content": '{"tier": "trivial", "justification": "single-line arithmetic bug"}'},
        {"content": "no tool needed yet"},
        {"content": "no tool needed yet"},
        {"content": "no tool needed yet"},
    ]
    for i in range(n_attempts):
        script.append({"content": f"attempt {i}", "tool": {"name": "open_file", "arguments": {"path": "calc.py"}}})
    return MockAdapter(script)


def test_budget_enforcement_forces_cutoff_mid_act(tmp_path, monkeypatch):
    repo_path, base_commit = _make_repo(tmp_path)

    # Starve the "trivial" profile down to almost nothing so Act's slice is
    # exhausted after a couple of tool calls, well before the 10-call script finishes.
    monkeypatch.setitem(
        budget_mod.BUDGET_PROFILES,
        "trivial",
        {"total_tokens": 200, "max_tool_calls": 4, "phase_split": budget_mod.PHASE_SPLIT},
    )

    trajectory = TrajectoryStore(str(tmp_path / "trajectory.jsonl"))
    config = RunConfig(repo_path=repo_path, base_commit=base_commit, issue_text="add() returns the wrong result")
    orchestrator = _make_orchestrator(_oversized_script(), config, trajectory, tmp_path)
    orchestrator.run()

    events = trajectory.read_all()
    enforced = [e for e in events if e["type"] == "budget_enforced"]

    assert enforced, "expected at least one budget_enforced event when the budget is starved"
    assert enforced[0]["phase"] == "act", f"expected the cutoff mid-Act, got phase={enforced[0]['phase']}"

    total_tool_calls = sum(1 for e in events if e["type"] == "tool_call")
    assert total_tool_calls < 10, "budget enforcement did not actually cut the run off early"


def test_naive_baseline_never_enforces(tmp_path, monkeypatch):
    repo_path, base_commit = _make_repo(tmp_path)
    monkeypatch.setitem(
        budget_mod.BUDGET_PROFILES,
        "complex",
        {"total_tokens": 200, "max_tool_calls": 4, "phase_split": budget_mod.PHASE_SPLIT},
    )

    trajectory = TrajectoryStore(str(tmp_path / "trajectory.jsonl"))
    config = RunConfig(repo_path=repo_path, base_commit=base_commit, issue_text="add() returns the wrong result")
    # naive_baseline skips the triage adapter call entirely, which shifts every later
    # script step by one slot vs. the non-naive path, and the retry loop can also eat
    # a step as a reflect message instead of a tool call -- use a generous number of
    # tool-bearing steps and assert on the total, not an exact phase-scoped count, so
    # the test isn't coupled to that incidental indexing.
    orchestrator = _make_orchestrator(_oversized_script(n_attempts=20), config, trajectory, tmp_path, naive_baseline=True)
    orchestrator.run()

    events = trajectory.read_all()
    assert not [e for e in events if e["type"] == "budget_enforced"]
    triage_events = [e for e in events if e["type"] == "triage"]
    assert triage_events and triage_events[0]["payload"]["skipped"] is True
    total_tool_calls = sum(1 for e in events if e["type"] == "tool_call")
    assert total_tool_calls >= 15, "naive baseline should run almost all tool-bearing steps with no budget-driven cutoff"
