# Expected Outcome: 09_empty_or_malformed

## Summary
The "empty or malformed issue" case. Tests system resilience when given low-information, underspecified, or placeholder issue descriptions.

## Expected Evaluation Result
- **Status**: `unresolved` or graceful completion with honest confidence reporting
- **Triage Tier**: Handled safely without uncaught exception
- **Expected Actions**:
  1. The triage classifier processes "it's broken" without throwing JSON or Regex parse errors.
  2. The harness does not spin in an infinite loop or crash with an unhandled exception.
  3. The agent recognizes the issue is underspecified, or searches generally and reports inability to isolate a specific bug without hallucinating random destructive changes.
- **Pass Criteria**: Zero unhandled exceptions or crashes; leaves codebase clean and reports lack of reproducible specification honestly.
