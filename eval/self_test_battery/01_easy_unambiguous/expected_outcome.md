# Expected Outcome: 01_easy_unambiguous

## Summary
The "easy, unambiguous" sanity baseline. A clearly scoped bug with an obvious single-file fix and an existing failing test.

## Expected Evaluation Result
- **Status**: `verified`
- **Triage Tier**: `trivial` or `moderate`
- **Expected Actions**:
  1. Localize to `more_itertools/more.py` (specifically `constrained_batches`).
  2. Apply a clean validation check for `max_count <= 0`.
  3. Verify via `tests/test_more.py::ConstrainedBatchesTests::test_nonpositive_max_count`.
  4. Both target test gate and regression test gates pass on first attempt.
  5. Retries used: `0`.
- **Pass Criteria**: Status is `verified`, target test passes, regression tests pass, and zero syntax errors.
