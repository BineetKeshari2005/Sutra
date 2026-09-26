"""Event-stream trajectory log: one JSON line per event. This is what the
Phase 1 timeline UI reads, and what the eval harness replays."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Event:
    timestamp: float
    phase: str
    type: str  # "phase_start" | "model_call" | "tool_call" | "observation" | "reflection" | "checkpoint" | "verify_gate" | "run_end"
    payload: dict[str, Any] = field(default_factory=dict)
    tokens_used: int = 0


class TrajectoryStore:
    def __init__(self, path: str):
        self.path = path

    def append(self, phase: str, type: str, payload: dict[str, Any] | None = None, tokens_used: int = 0) -> Event:
        event = Event(timestamp=time.time(), phase=phase, type=type, payload=payload or {}, tokens_used=tokens_used)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event)) + "\n")
        return event

    def read_all(self) -> list[dict[str, Any]]:
        events = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except FileNotFoundError:
            pass
        return events
