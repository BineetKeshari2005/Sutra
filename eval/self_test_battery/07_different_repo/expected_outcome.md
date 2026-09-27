# Expected Outcome: 07_different_repo

## Summary
The "different repo entirely" case. Tests Sutra against a brand new, unseen Python project (`strutils`) with its own directory structure, dependencies, and test suite, proving zero overfitting to `more-itertools`.

## Expected Evaluation Result
- **Status**: `verified`
- **Triage Tier**: `trivial` or `moderate`
- **Expected Actions**:
  1. The agent searches or opens `strutils/slug.py`.
  2. The agent modifies `slugify()` to use `re.sub(r"\s+", separator, text)`.
  3. The verification gate runs `pytest tests/test_slug.py::test_consecutive_spaces_collapsed` and regression suite `tests/test_slug.py`.
  4. All tests pass with zero failures.
- **Pass Criteria**: `status == "verified"`, target test passes, regression tests pass.
