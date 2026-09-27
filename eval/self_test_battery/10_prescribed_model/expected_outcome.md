# Expected Outcome: 10_prescribed_model

## Summary
The "prescribed model, not our usual one" case. Validates that tool-calling, budget routing, syntax verification, and reflection execute reliably under the officially configured model (`AI_API_KEY` model, e.g. Gemini 3.1 Flash Lite or prescribed model).

## Expected Evaluation Result
- **Status**: `verified`
- **Triage Tier**: `trivial` or `moderate`
- **Expected Actions**:
  1. The configured model adapter processes tools, parses JSON tool calls cleanly without syntax or malformed function call errors.
  2. Completes localization, editing, and test gates within budget.
  3. Outputs clean diff.
- **Pass Criteria**: `status == "verified"`, zero model adapter serialization errors, test gates pass.
