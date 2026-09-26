"""Proves enforce_ceiling actually invokes the summarizer and shrinks the
message list once a phase's spend crosses the threshold -- not merely that
the function was called."""
from __future__ import annotations

import subprocess

from harness.context_manager.budgeter import KEEP_RECENT_MESSAGES, enforce_ceiling
from harness.memory.repo_memory import RepoMemory, repo_id_for
from harness.memory.trajectory_store import TrajectoryStore
from harness.model_adapter.base import Message
from harness.model_adapter.mock_adapter import MockAdapter
from harness.orchestrator.state_machine import Orchestrator, RunConfig


def _synthetic_phase_messages(n_pairs: int) -> list[Message]:
    """A phase's own instruction message followed by n_pairs of
    (assistant-with-tool-call, tool-result) messages, each padded to be
    genuinely large -- like a real open_file dump would be."""
    messages = [Message(role="user", content="phase instruction")]
    big_content = "x" * 2000  # ~500 tokens per message at a rough 4-chars/token estimate
    for i in range(n_pairs):
        messages.append(Message(role="assistant", content=f"turn {i}: {big_content}"))
        messages.append(Message(role="tool", content=f"observation {i}: {big_content}", tool_call_id=str(i), name="open_file"))
    return messages


def test_enforce_ceiling_does_nothing_below_threshold():
    messages = _synthetic_phase_messages(10)
    new_messages, info = enforce_ceiling(
        messages, phase_start_idx=0, spent_tokens=100, max_tokens=10_000, adapter=MockAdapter([]),
    )
    assert info is None
    assert new_messages == messages


def test_enforce_ceiling_does_nothing_when_history_too_short():
    messages = _synthetic_phase_messages(1)  # instruction + 1 pair = 3 messages, below KEEP_RECENT_MESSAGES
    new_messages, info = enforce_ceiling(
        messages, phase_start_idx=0, spent_tokens=9_000, max_tokens=10_000, adapter=MockAdapter([]),
    )
    assert info is None
    assert new_messages == messages


def test_enforce_ceiling_summarizes_and_shrinks_history_above_threshold():
    messages = _synthetic_phase_messages(10)  # instruction + 20 messages = 21 total
    original_len = len(messages)
    original_chars = sum(len(m.content) for m in messages)

    adapter = MockAdapter([{"content": "a compact summary of the older turns"}])
    new_messages, info = enforce_ceiling(
        messages, phase_start_idx=0, spent_tokens=6_500, max_tokens=10_000, adapter=adapter,
    )

    assert info is not None
    assert info["collapsed_messages"] > 0
    # instruction + 1 summary message + the kept recent tail
    assert len(new_messages) == 1 + 1 + KEEP_RECENT_MESSAGES
    assert len(new_messages) < original_len
    new_chars = sum(len(m.content) for m in new_messages)
    assert new_chars < original_chars  # genuinely cheaper, not just fewer messages
    assert new_messages[0].content == "phase instruction"  # instruction message preserved verbatim
    assert "summary of" in new_messages[1].content


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


def test_orchestrator_logs_context_summarized_event_when_a_phase_runs_long(tmp_path, monkeypatch):
    """End-to-end: a starved act-phase budget with a long, large-observation
    tool loop should trigger a real context_summarized event, not just the
    unit-level enforce_ceiling check above."""
    from harness.orchestrator import budget as budget_mod

    monkeypatch.setitem(
        budget_mod.BUDGET_PROFILES,
        "trivial",
        {"total_tokens": 20_000, "max_tool_calls": 30, "phase_split": budget_mod.PHASE_SPLIT},
    )

    repo_path, base_commit = _make_repo(tmp_path)
    big = "x" * 1500

    script = [
        {"content": '{"tier": "trivial", "justification": "test"}'},
        {"content": "restating the bug"},
        {"content": "no search needed"},
        {"content": "plan: fix it"},
    ]
    for i in range(14):
        script.append({"content": f"attempt {i}: {big}", "tool": {"name": "open_file", "arguments": {"path": "calc.py"}}})
    script.append({"content": "giving up for this test"})
    script.append({"content": '{"conventions": [], "landmines": [], "fix_patterns": []}'})
    script.append({"content": '{"root_cause_confidence": "low", "root_cause_summary": "x", "unresolved_issue": "x"}'})

    memory = RepoMemory(repo_id_for(repo_path), store_dir=str(tmp_path / "memstore"))
    trajectory = TrajectoryStore(str(tmp_path / "trajectory.jsonl"))
    config = RunConfig(repo_path=repo_path, base_commit=base_commit, issue_text="add() is wrong")
    orchestrator = Orchestrator(MockAdapter(script), config, trajectory, repo_memory=memory, max_retries=0)
    orchestrator.run()

    events = trajectory.read_all()
    summarized = [e for e in events if e["type"] == "context_summarized"]
    assert summarized, "expected at least one context_summarized event on a long, budget-heavy phase"
    assert summarized[0]["payload"]["collapsed_messages"] > 0
