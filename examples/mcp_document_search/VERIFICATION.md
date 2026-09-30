# Recorded local integration validation

Validation date: 2026-10-01 (Pacific/Auckland).

Environment: Python 3.12.14, official MCP Python SDK 2.2.0, negotiated protocol `2026-07-28`, macOS, separate server process using stdio. AgentTrace Lab source was exercised directly from the checkout.

The fixed acceptance command completed **28 assertions**, including expected negative outcomes:

- A real search and document read produced 2 recorded calls and no errors.
- Repeating the read produced 3 calls; comparison detected `tool_calls_regression`, and the comparison CLI exited `1`.
- A missing-file RPC returned `isError=true`. The raw SDK wrapper recorded 0 exceptions; the explicit adapter recorded 1 `MCPToolError` and failed an error budget of zero.
- Empty search results stayed successful. All 12 invalid/disallowed-input probes were rejected over MCP and appeared as errors in the trace. Path checks covered parent traversal, absolute paths, a symlinked file, a symlinked ancestor, hidden files, and unsupported suffixes; file-content checks covered size and invalid UTF-8.
- All six trace files omitted captured arguments, results, and exception messages. Search-result files intentionally contain document excerpts and need review before sharing.
- Each inspection/comparison produced portable HTML and JSON output.

A separate real search against the project's `docs` directory queried `run_start` and returned `TRACE_FORMAT.md` with the matching schema-table line. This checks use on actual project documentation as well as the included fixtures.

The first integration attempt exposed a server-output issue: a bare `dict` annotation produced text content without `structured_content`. Explicit result models fixed the contract, and the subsequent real stdio run passed.

The final run was repeated after the core audit/comparison changes. Reproduce with the [README commands](README.md#run-the-acceptance-checks). The runner writes `acceptance.json`, `discovered-tools.json`, individual `.jsonl` traces, and report directories to the chosen output directory. Generated artifacts contain run IDs and timings, and may contain local paths in captured CLI output; they are ignored by Git. No user's absolute filesystem path is published in this note.

These results establish this example's local behavior with the stated SDK and runtime. They do not establish model quality, general MCP compatibility, production hardening, speed improvements, or external-user adoption.
