# Expected Outcome: 08_larger_real_world_file

## Summary
The "larger real-world file" case. Exercises Sutra's summarizer and file size-capping safeguards on a real-world frontend repository where files exceed 200+ dense lines.

## Expected Evaluation Result
- **Status**: `verified` (clean diff generated)
- **Triage Tier**: `moderate` or `complex`
- **Expected Actions**:
  1. The agent locates relevant files (`src/pages/Search.jsx` / state management).
  2. Large file opens trigger file-ops summarizer / chunking rather than overflowing context.
  3. Edits apply cleanly using target content matching without corrupting the file structure.
  4. Generates a valid git diff.
- **Pass Criteria**: No token limit or context window blowout errors; diff cleanly addresses query persistence.
