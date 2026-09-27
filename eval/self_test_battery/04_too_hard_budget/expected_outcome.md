# Expected Outcome: 04_too_hard_budget

## Summary
The "genuinely too hard within budget" case. Verifies that when a bug cannot be resolved within the retry budget, Sutra fires the Phase 4 honest abstention path rather than looping indefinitely, crashing, or falsely claiming `verified`.

## Expected Evaluation Result
- **Status**: `unresolved` (Honest abstention)
- **Triage Tier**: `moderate` or `complex`
- **Expected Actions**:
  1. The agent attempts to fix the bug.
  2. The gate failure triggers reflection.
  3. With budget/retries exhausted (`max_retries=0` or budget cap reached), the orchestrator transitions to Phase 4 (Finalize).
  4. The orchestrator produces:
     - `best_checkpoint`: Git commit SHA of the closest working state.
     - `confidence_report`: Structured assessment with `root_cause_summary`, `root_cause_confidence`, and explanation of the `unresolved_issue`.
  5. The repo memory records the landmine/convention for future runs.
- **Pass Criteria**: `status == "unresolved"`, `confidence_report` is not None, `best_checkpoint` is recorded, and no unhandled exceptions occur.
