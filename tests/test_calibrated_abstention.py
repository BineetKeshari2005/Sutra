import subprocess

from harness.memory.repo_memory import RepoMemory, repo_id_for
from harness.memory.trajectory_store import TrajectoryStore
from harness.model_adapter.mock_adapter import MockAdapter
from harness.orchestrator.state_machine import Orchestrator, RunConfig


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


def _make_orchestrator(adapter, config, trajectory, tmp_path, **kwargs):
    memory = RepoMemory(repo_id_for(config.repo_path), store_dir=str(tmp_path / "memstore"))
    return Orchestrator(adapter, config, trajectory, repo_memory=memory, **kwargs)


def test_unresolved_run_submits_best_checkpoint_with_confidence_report(tmp_path):
    """A plausible-but-wrong single attempt (real gate failure, max_retries=0)
    must produce status=unresolved, a non-empty diff of the best checkpoint
    reached, and a real confidence report -- never a silent failure and never
    the broken patch presented as if it were done."""
    repo_path, base_commit = _make_repo(tmp_path)

    script = [
        {"content": '{"tier": "trivial", "justification": "single function"}'},
        {"content": "Restating the bug."},
        {"content": "add() is in calc.py, no search needed."},
        {"content": "Plan: change the operator."},
        # A real edit that is STILL wrong (doesn't fix the bug) -- a genuine gate failure follows.
        {"content": "Applying a fix.", "tool": {"name": "edit_file", "arguments": {"path": "calc.py", "old_str": "return a - b  # bug: should be +", "new_str": "return a - b - 1  # still wrong"}}},
        {"content": "Running tests.", "tool": {"name": "run_tests", "arguments": {}}},
        {"content": "Still failing."},
        {"content": '{"conventions": [], "landmines": [], "fix_patterns": []}'},
        {"content": '{"root_cause_confidence": "low", "root_cause_summary": "wrong operator", "unresolved_issue": "still subtracting"}'},
    ]

    trajectory = TrajectoryStore(str(tmp_path / "trajectory.jsonl"))
    config = RunConfig(repo_path=repo_path, base_commit=base_commit, issue_text="add() returns the wrong result", target_test="test_calc.py")
    orchestrator = _make_orchestrator(MockAdapter(script), config, trajectory, tmp_path, max_retries=0)
    result = orchestrator.run()

    assert result.status == "unresolved"
    assert result.verified is False
    assert result.best_checkpoint is not None
    assert result.diff.strip() != ""  # the best (even if wrong) attempt, never nothing
    assert result.confidence_report is not None
    assert result.confidence_report["attempts_made"] == 1
    assert result.adversarial_review is None  # never runs on an unresolved outcome

    events = trajectory.read_all()
    assert any(e["type"] == "confidence_report" for e in events)
    assert not any(e["type"] == "adversarial_review" for e in events)


def test_verified_run_gets_adversarial_review_not_confidence_report(tmp_path):
    repo_path, base_commit = _make_repo(tmp_path)

    script = [
        {"content": '{"tier": "trivial", "justification": "single function"}'},
        {"content": "Restating the bug."},
        {"content": "add() is in calc.py, no search needed."},
        {"content": "Plan: fix the operator."},
        {"content": "Applying the fix.", "tool": {"name": "edit_file", "arguments": {"path": "calc.py", "old_str": "return a - b  # bug: should be +", "new_str": "return a + b"}}},
        {"content": "Running tests.", "tool": {"name": "run_tests", "arguments": {}}},
        {"content": "Tests pass."},
        {"content": '{"conventions": [], "landmines": [], "fix_patterns": []}'},
        {"content": '{"verdict": "no_concerns", "notes": "minimal, correct fix"}'},
    ]

    trajectory = TrajectoryStore(str(tmp_path / "trajectory.jsonl"))
    config = RunConfig(repo_path=repo_path, base_commit=base_commit, issue_text="add() returns the wrong result", target_test="test_calc.py")
    orchestrator = _make_orchestrator(MockAdapter(script), config, trajectory, tmp_path)
    result = orchestrator.run()

    assert result.status == "verified"
    assert result.confidence_report is None
    assert result.adversarial_review == {"verdict": "no_concerns", "notes": "minimal, correct fix"}

    events = trajectory.read_all()
    assert any(e["type"] == "adversarial_review" for e in events)
    assert not any(e["type"] == "confidence_report" for e in events)


def test_pick_best_attempt_prefers_higher_gate_pass_count():
    attempts = [
        {"commit": "c1", "gate_result": {"gates": {"a": {"passed": True}, "b": {"passed": False}}}},
        {"commit": "c2", "gate_result": {"gates": {"a": {"passed": True}, "b": {"passed": True}}}},
        {"commit": "c3", "gate_result": {"gates": {"a": {"passed": False}, "b": {"passed": False}}}},
    ]
    best = Orchestrator._pick_best_attempt(attempts)
    assert best["commit"] == "c2"


def test_pick_best_attempt_breaks_ties_toward_later_attempt():
    attempts = [
        {"commit": "c1", "gate_result": {"gates": {"a": {"passed": True}, "b": {"passed": False}}}},
        {"commit": "c2", "gate_result": {"gates": {"a": {"passed": False}, "b": {"passed": True}}}},
    ]
    best = Orchestrator._pick_best_attempt(attempts)
    assert best["commit"] == "c2"
