# Contributing

AgentTrace Lab focuses on local Python tool traces that are easy to record, inspect, and compare.

For a bug report, include a minimal Python example or small redacted v1 JSONL trace, the command, expected and actual results, and Python/project versions. Do not post credentials or unreviewed value captures.

For changes:

1. Keep the standard-library core usable without a model provider or network access.
2. Preserve the documented trace schema, or propose a versioned migration.
3. Add a focused test, then run `python3 -m unittest discover -s tests -v`.
4. Run `python3 -m agent_trace_lab demo --output artifacts/demo` and check generated JSON reports.
5. Explain the practical problem and remaining limitations in the pull request.

Useful contributions include optional SDK format adapters, regression cases from your own tools, accessibility improvements, and recorder-overhead measurements. Preserve third-party attribution and licenses.

Do not present trace-budget checks as answer-quality evaluation. Provider integrations should state which versions and live calls were tested.
