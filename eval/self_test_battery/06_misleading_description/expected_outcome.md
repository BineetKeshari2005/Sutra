# Expected Outcome: 06_misleading_description

## Summary
The "misleading issue description" case. The issue text points to the wrong file (`more_itertools/recipes.py`), but the actual symbol is defined in `more_itertools/more.py`.

## Expected Evaluation Result
- **Status**: `verified`
- **Triage Tier**: `trivial` or `moderate`
- **Expected Actions**:
  1. If the agent first opens `more_itertools/recipes.py`, it does not find `constrained_batches`.
  2. The agent uses `search_symbol("constrained_batches")` or `search_code("def constrained_batches")` to discover the true definition in `more_itertools/more.py`.
  3. The agent edits `more_itertools/more.py` (leaving `recipes.py` untouched).
  4. The test gate `tests/test_more.py::ConstrainedBatchesTests::test_nonpositive_max_count` passes.
- **Pass Criteria**: Status is `verified`, only `more_itertools/more.py` is modified, and tests pass.
