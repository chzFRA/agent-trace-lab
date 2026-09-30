# Trace format v1

`TraceSession` instruments actual Python calls. It does not simulate an LLM,
make network requests, or judge answer quality. Files are UTF-8 JSONL: one
complete JSON object per event, flushed after each event. A single session owns
one file; files are created exclusively unless `overwrite=True` is explicit.
`capture_values` and `overwrite` require actual booleans; strings such as
`"false"` and integers are rejected. Run and tool names must be nonempty strings
of at most 256 valid Unicode characters, including a decorated function's default name.
Unpaired Unicode surrogates in names are rejected; captured strings replace them.

```python
from agent_trace_lab.tracing import TraceSession

with TraceSession("run.jsonl", name="research") as session:
    @session.tool()
    def retrieve(query):
        return {"query": query, "documents": []}

    retrieve("agent memory")
```

All events have `schema_version: 1`, `type`, a random `run_id`, a consecutive
integer `seq` starting at 1, and an ISO 8601 UTC `timestamp` with `+00:00` offset.
Durations use a monotonic performance counter and are milliseconds.

| Event | Additional fields |
| --- | --- |
| `run_start` | `name`, `capture_values` (boolean) |
| `tool_start` | `call_id`, `parent_id` (call ID or null), `tool`; optional `arguments` |
| `tool_end` | `call_id`, `tool`, `status` (`ok`, `error`, `cancelled`), `duration_ms`; optional `result`, `error` |
| `run_end` | `status` (`ok`, `error`), `duration_ms`; `unfinished_calls` only when calls remain active |

An error contains `type` and, only with value capture enabled, `message`.
Caught tool failures do not make the overall run fail: run status reflects
whether an exception escapes the context, or calls remain unfinished.
Async cancellation records a cancelled tool; cancellation escaping the context
records an error run. Process crashes and recording failures may leave incomplete
files. This format has no retry/LLM/token/cost semantics; repeated tool names alone
do not establish that a retry occurred.

## Concurrency and supported tools

`session.tool(name=None)` preserves function metadata and inspectable signatures,
the original return object, and raised exception instances. Ordinary functions
and `async def` coroutines are supported. Generator and async-generator functions
are rejected because their creation does not execute their bodies. Streaming SDK
objects returned by an ordinary function are not consumed or traced internally.

Events from threads are serialized under a lock. Parent IDs follow Python context
variables: nested sync/async calls and inherited asyncio task contexts retain their
parent. Plain threads start without that context unless the caller propagates it.
Instrumentation adds locking, serialization, and synchronous filesystem I/O.

Decorators can be created before entering a session, but calls must occur inside
its context. A session is single-use. Join threads and await tasks before context
exit. Premature exit records `unfinished_calls`, closes the file, and raises
`RuntimeError` if there was no original exception; it preserves an already active
exception. Completing those calls afterward does not append to the closed file.

## Privacy and failure policy

Default capture stores tool names, timestamps, status, duration, IDs, and exception
class names. It does **not** store argument values, results, or error messages.
Run and tool names are caller-supplied metadata: keep secrets out of names.

`capture_values=True` records bounded summaries. Arguments are matched to parameter
names where available. Sensitive dictionary/parameter keys (case-insensitive API
key, token, authorization, password, secret, credential, private key) are masked.
Bearer strings and common `token=value`-style text are masked. Summaries limit
strings to 256 characters plus a marker, collections to 20 entries plus a marker,
nesting to 4 levels, and traversal to 128 nodes. Custom objects are represented by
type name, never their `repr`; binary values record length only. Non-finite floats
and very large integers become markers. A summary whose escaped JSON exceeds
64 KiB is replaced with `[summary size limit: 64 KiB]`, keeping captured events
within the inspector's line-size limit even for Unicode-heavy dictionaries.
Type names and exception class names are bounded to 256 characters plus a marker.
Summaries are lossy observations, not
replayable arguments. Signature-less callables use positional/keyword grouping;
positional secrets then have no parameter name to detect.

**Redaction is best effort, not arbitrary secret or PII detection.** Credentials in
free text, unknown key names, URLs, names, emails, document text, or tool metadata
may remain. Inspect captured traces before sharing; leave capture disabled for
sensitive workloads. Nothing is uploaded by this library.

File opening and the initial `run_start` write fail immediately. Once active,
recording/closing errors produce `RuntimeWarning` and appear as exception class
names in `session.logging_errors`. The first recording error disables subsequent
writes. Even warning filters configured to raise will not replace a tool result or
its original exception. A run with logging errors must be treated as incomplete,
even if its tool calls succeeded. JSONL flushing is not an `fsync` durability
guarantee; a partial final line can occur if storage fails during a write.

The recorder and reader share a 1 MiB line limit and a 20 MiB file limit. Once a
write would exceed a limit, `TraceLimitError` is recorded in `logging_errors`,
further recording stops, and tools continue with their original outcomes. This
leaves an incomplete run rather than a complete-looking file the reader rejects
for size. Use one session per task; there is no automatic log rotation. A close
error after a successfully flushed `run_end` may leave a valid-looking file: an
offline audit cannot reconstruct that failure, so check the runtime diagnostics.
If initialization and cleanup both fail, the initialization exception is preserved.

## Import and audit rules

Imported JSON is limited to 32 levels including the event object. Strings and keys
must contain valid Unicode; numbers must be finite. The audit rejects contradictory
records: a tool duration exceeding the whole run, or arguments/results/error
messages present when `capture_values` is false. These checks detect inconsistency,
not authenticity: traces are unsigned and can be edited.

Handled errors and cancellations are counts until `max_errors` / `max_cancelled`
is set. A failed application run or structurally incomplete trace always fails.
Cancellation is distinct from error, and a caught cancellation need not fail the
whole application.

Comparison outputs include `per_tool_deltas` with named-tool `before`, `after`,
and `deltas` objects for `calls`, `errors`, `cancelled`, and `duration_ms`. A tool
absent from either run has zero metrics on that side. Run totals are always
checked; `per_tool=True` applies the same count/error/cancellation tolerances to
each named tool too. The optional duration ratio applies only to total run time.
Renaming or adding a tool may intentionally fail a per-tool call budget; review
the report before adjusting tolerances. Comparisons do not verify matching inputs,
result correctness, or whether a repeated call was logically unnecessary.
