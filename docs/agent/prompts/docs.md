## Docs-Specific Guidance

- **No pytest needed**: If your change touches ONLY documentation/HTML/markdown, you may skip the full pytest suite.
- **Still run the validation gate**: `bash scripts/ralph/ralph_validate.sh --tier=smoke` is sufficient for doc-only changes.
- **Consistency**: Ensure terminology matches the rest of the docs.
- **Accuracy**: Verify all CLI commands, file paths, and version numbers are current.
