## Bugfix-Specific Guidance

- **Root cause first**: Understand WHY the bug happens before writing code.
- **Minimal change**: Fix the root cause with the smallest possible diff. Do not refactor adjacent code.
- **Regression test**: If no test covers this bug, add one in `tests/regression/` or `tests/unit/`.
- **Reproduce before fix**: Ensure you can reproduce the bug (via test or manual run) before applying the fix.
- **No band-aids**: Avoid workarounds that mask symptoms. Fix the actual cause.
