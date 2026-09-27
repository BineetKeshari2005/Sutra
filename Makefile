.PHONY: setup run test clean

SHELL := /bin/bash

setup:
	@echo "Setting up Sutra..."
	python3 -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt

run:
	@echo "Starting Sutra..."
	. .venv/bin/activate && AI_API_KEY="$(AI_API_KEY)" python demo/run_demo.py

test:
	@echo "Running Sutra's test suite..."
	. .venv/bin/activate && AI_API_KEY="$(AI_API_KEY)" python -m pytest tests/ -q

clean:
	@echo "Cleaning up..."
	rm -rf .venv
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -f demo/trajectory.jsonl
	rm -rf /tmp/sutra-sandbox-* /tmp/agent-run-*
