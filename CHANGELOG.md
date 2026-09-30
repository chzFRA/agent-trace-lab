# Changelog

## 0.3.0 — 2026-10-01

- Add a real optional MCP stdio document-search integration and executable fault-detection acceptance checks.
- Expose MCP `isError` failures through an explicit example adapter, with a raw-client comparison proving why it is needed.
- Show changes by named tool; add opt-in per-tool regression checks and cancellation budgets.
- Make parent tool names readable in HTML reports and add a Chinese first-use tutorial.
- Preserve initial recording errors if cleanup also fails; stop recording before unsupported file sizes while preserving tool outcomes.
- Reject inconsistent capture policies, impossible durations, malformed Unicode, and deeply nested trace inputs.
- Add focused reliability/CLI regressions and a reproducible local overhead measurement script.

## 0.2.0 — 2026-09-30

- Record real synchronous, asynchronous, nested, and threaded Python calls.
- Keep value capture off by default; offer bounded summaries and common credential masking when opted in.
- Audit v1 JSONL traces for structural problems and post-run budgets.
- Compare call/error counts, with optional duration checks and CI exit codes.
- Generate portable HTML reports and real file-tool examples.
- Add a Chinese learning path with official upstream references and exercises.
- Retain the v0.1 synthetic executor lessons under `run`.

## 0.1.0 — local prototype

- Scripted retrieval policy and eight synthetic executor scenarios.
- Tool validation, bounded retry demonstration, and synthetic reports.

No hosted releases or real-model performance results are implied by these entries.
