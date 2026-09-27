# Expected Outcome: 05_already_fixed

## Summary
The "issue is already fixed / not reproducible" case. A prompt claiming a bug that doesn't actually exist on the target codebase.

## Expected Evaluation Result
- **Status**: `not_reproduced` / `clean_pass` (no harmful edits)
- **Triage Tier**: `trivial`
- **Expected Actions**:
  1. The agent inspects `collate()` in `more_itertools/more.py`.
  2. The agent runs or checks existing tests in `tests/test_more.py::CollateTests`.
  3. Tests pass immediately because `heapq.merge` already handles empty iterables cleanly.
  4. The harness does NOT hallucinate artificial code breaking existing behavior.
- **Pass Criteria**: Existing test suite continues to pass 100%; no destructive or unnecessary mutations applied.
