## Regression-Test-Specific Guidance

- **Invariant focus**: Identify the exact behavior that must be protected against future regressions.
- **Fail-before-fix**: The regression test should ideally fail against the buggy code and pass after the fix.
- **Edge cases**: Cover boundary conditions (empty input, max values, None, exceptions).
- **No mocks unless needed**: Use real dependencies where possible. Mock only external APIs or slow I/O.
- **Location**: Place the test in `tests/regression/` if it covers cross-module behavior, or `tests/unit/` if scoped to a single module.
