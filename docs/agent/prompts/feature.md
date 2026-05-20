## Feature-Specific Guidance

- **Plan before code**: Write a brief implementation plan in your reasoning. Identify the ≤3 files you will touch.
- **Backward compatibility**: Do not break existing APIs or behavior unless explicitly required.
- **Tests first**: Add unit tests for new behavior. If the feature is large, stop after 3 files and document remaining work.
- **Documentation**: Update `docs/user/` docs if the feature is user-facing. Update agent docs if behavior contracts change.
- **Integration**: Verify the new feature integrates cleanly with existing modules.
