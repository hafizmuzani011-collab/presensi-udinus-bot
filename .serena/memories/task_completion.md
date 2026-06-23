# Task Completion Verification

Before reporting any coding task as done, these commands must pass:
- `ruff check .` (zero remaining errors, W291/ruff_fix only)
- `pytest` (all tests passing)

Lint errors may require manual fix after running `ruff check --fix .` first.
