## Operations-Specific Guidance

- **Pre-flight check**: Verify any external service connectivity before making changes.
- **Real data only**: NEVER use `--dry-run`, synthetic data, or mock responses for production paths unless explicitly permitted.
- **Audit trail**: Record changes, timestamps, and decisions in `docs/qa_reports/` or `PROGRESS.md`.
- **Kill switch**: Respect any active halt mechanisms. If active, abort immediately.
- **Staging first**: When possible, test operational changes in a staging environment before production.
