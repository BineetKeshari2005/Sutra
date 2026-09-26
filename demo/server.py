"""Tiny FastAPI server for the trajectory timeline UI.

GET  /api/trajectory?run=<name>   parsed JSONL as a JSON array (default: the
                                   committed more-itertools fixture)
GET  /api/trajectory/live         SSE stream that tails demo/trajectory.jsonl
                                   as new lines are appended (live-run mode,
                                   nice-to-have; the fixture is the primary path)
GET  /                            demo/ui/index.html and its static assets
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(DEMO_DIR, "fixtures")
LIVE_TRAJECTORY_PATH = os.path.join(DEMO_DIR, "trajectory.jsonl")
DEFAULT_FIXTURE = "more-itertools-trajectory"

app = FastAPI()


def _read_jsonl(path: str) -> list[dict]:
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


@app.get("/api/trajectory")
def get_trajectory(run: str = DEFAULT_FIXTURE):
    safe_name = os.path.basename(run)  # no path traversal via the query param
    path = os.path.join(FIXTURES_DIR, f"{safe_name}.jsonl")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"no fixture named '{safe_name}'")
    return _read_jsonl(path)


@app.get("/api/trajectory/live")
async def stream_live_trajectory():
    async def event_source():
        if not os.path.isfile(LIVE_TRAJECTORY_PATH):
            yield f"event: error\ndata: {json.dumps({'error': 'no live trajectory.jsonl yet'})}\n\n"
            return
        with open(LIVE_TRAJECTORY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield f"data: {line}\n\n"
            # tail: poll for new lines appended by an in-progress run
            for _ in range(600):  # ~5 minutes at 0.5s poll
                line = f.readline()
                if line.strip():
                    yield f"data: {line.strip()}\n\n"
                else:
                    await asyncio.sleep(0.5)

    return StreamingResponse(event_source(), media_type="text/event-stream")


app.mount("/", StaticFiles(directory=os.path.join(DEMO_DIR, "ui"), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8008)
