# Trace a real MCP document-search service

This example starts the official MCP Python SDK server in a **separate process**, connects through **stdio**, and traces the client's tool calls. It searches real local text files. It needs no model, API key, HTTP server, or desktop application.

The practical problem: an MCP tool can fail while the Python RPC call returns normally. A plain exception-based tracer then reports success. The example reproduces that failure mode, shows the adapter needed to record it correctly, and catches an extra read introduced into the same workflow.

[中文说明](#中文说明) · [AgentTrace Lab](../../README.md) · [Learning path](../../docs/LEARNING_PATH.zh-CN.md)

## Run the acceptance checks

Use a checkout of AgentTrace Lab and run **from the repository root**, the directory containing `pyproject.toml`:

```bash
python3.12 -m venv examples/mcp_document_search/.venv
examples/mcp_document_search/.venv/bin/python -m pip install -r examples/mcp_document_search/requirements.txt
examples/mcp_document_search/.venv/bin/python examples/mcp_document_search/run.py verify --output artifacts/mcp-acceptance
```

The SDK is pinned to **`mcp==2.2.0`**. The SDK requires Python 3.10+; this example was verified with Python 3.12.14 on macOS. The commands above target macOS/Linux; the acceptance checks create a temporary symlink to verify escape rejection. AgentTrace Lab itself still supports its documented Python versions and does not require MCP.

The first command uses your Python 3.12 interpreter; substitute its path if it is not named `python3.12`. Dependency installation requires package-index access once. Subsequent example runs are local. The server is automatically started and stopped by the client. Choose a **new output directory** when repeating a run; existing outputs are not overwritten.

Successful acceptance exits `0` and reports `PASS`. Open:

- `artifacts/mcp-acceptance/acceptance.json` for individual assertions and runtime versions.
- `artifacts/mcp-acceptance/comparison/report.html` for the intentionally repeated call.
- `artifacts/mcp-acceptance/adapter-error/report.html` for the observed MCP tool error.

Each scenario also saves its JSONL trace and HTML/JSON inspection reports. Generated files live in ignored `artifacts/` directories.

## What is actually verified

The fixed suite uses two included, non-sensitive documents. Assertions inspect the returned content, discovered input schemas, MCP error flag, recorded call/error counts, regression reports, and the CLI's exit status. No client or server is mocked.

| Scenario | Measured calls / errors | Expected result |
| --- | --- | --- |
| Search `retry attempts`, read its result | 2 / 0 | `reliability.md` is found and its expected sentence is returned |
| Repeat that same document read | 3 / 0 | Comparison rejects the extra call; comparison CLI exits `1` |
| Search a word absent from the documents | 1 / 0 | Empty matches are a successful search |
| Read missing file with raw SDK call | 1 / 0 | MCP reports `isError=true`, while the raw wrapper records no Python exception |
| Read missing file through adapter | 1 / 1 | `MCPToolError` is recorded; an error budget of zero fails |
| Twelve invalid or disallowed requests | 12 / 12 | Bad queries/limit, parent traversal, absolute path, symlink file/ancestor, hidden file, unsupported suffix, large file, and invalid UTF-8 are rejected |

The **expected negative outcomes are acceptance successes**: the suite passes when the bad run fails its budget and the regression CLI exits `1`. A failure to detect these cases makes the suite fail. The rejection trace is structurally complete even though its calls failed; it has no error budget in its structural inspection. The suite also checks that the six trace files omit captured inputs, results, and exception messages.

Baseline and repeated-run timings include SDK/runtime warmup effects. This suite checks counts and error semantics, **not latency improvements**, model reasoning, or answer accuracy. It is a local integration test, not feedback from external users. See [the recorded validation note](VERIFICATION.md).

## Search your own project documentation

From the repository root, search this project's actual format documentation:

```bash
examples/mcp_document_search/.venv/bin/python examples/mcp_document_search/run.py search "run_start" --root docs --output artifacts/mcp-project-docs
```

The result includes a relative document path, a 1-based line number, and a short matching excerpt. This query should find `TRACE_FORMAT.md`; line numbers may change as the project evolves. Results are also saved as `search-results.json`, separately from the trace. Choose a directory of non-sensitive `.md` and `.txt` files if you substitute your own `--root`.

Search is a deterministic lexical baseline: every query word must occur on the same line, ignoring case; the first matching line per document is returned in path order. It is not semantic search or RAG. The service reads at most 128 documents, each at most 128 KiB, with up to 10 results. Queries are limited to 240 characters and 16 words. Hidden paths, absolute paths, `..`, unsupported file types, and symlinked files/ancestors are rejected. The server does not write documents. These checks suit a trusted local document folder; they are not an OS sandbox against hostile concurrent filesystem changes.

Trace value capture stays **off**: arguments, returned text, and exception messages are omitted from JSONL. Search results intentionally contain excerpts; review those files before sharing.

## Why the adapter matters

In MCP 2.x, the Python result attribute is `is_error`; the JSON protocol field is `isError`. The [official client documentation](https://py.sdk.modelcontextprotocol.io/client/) explains that a server tool exception normally returns an error result. It does not necessarily raise at the client. [The official error guide](https://py.sdk.modelcontextprotocol.io/servers/handling-errors/) distinguishes this from a JSON-RPC error.

[`adapter.py`](adapter.py) checks the flag **inside the traced function**:

```python
result = await client.call_tool(tool, arguments)
if result.is_error:
    raise MCPToolError(f"MCP tool {tool!r} returned isError=true")
```

The caller can still catch `MCPToolError` and recover. The trace now retains the failed tool call. Protocol/transport exceptions also propagate normally. This adapter intentionally converts semantics; `TraceSession` itself still preserves the wrapped Python function's behavior.

The server uses explicit Pydantic result models so the SDK advertises an output schema and supplies `structured_content`. The adapter rejects an unexpected non-object success payload instead of silently trusting it.

The [official SDK](https://github.com/modelcontextprotocol/python-sdk) supplies the MCP implementation; this directory supplies the document tools, adapter, and acceptance workflow. The [stdio transport guide](https://py.sdk.modelcontextprotocol.io/client/transports/#stdio) describes the subprocess boundary used here. This is a small integration example, not an automatic adapter for arbitrary MCP servers or all SDK versions.

## 中文说明

这个例子解决一个实际问题：MCP 工具执行失败时，客户端可能正常返回一个带 `isError=true` 的结果。如果只在 Python 层记录异常，就会误记为成功。适配器先检查错误标记，再在被追踪的函数内抛出明确异常，调用者仍然可以捕获和恢复。

上面的 `verify` 命令会启动真正的 MCP 子进程，验证固定案例。重复读取应该使比较失败；缺失文件应该被记录为错误；没有搜索结果仍是正常调用。看到某份报告写着失败，不一定代表验收失败——这些负例正是要证明检查生效。

`search` 命令可直接搜索本项目 `docs`，也可用 `--root` 指定自己的非敏感文档目录。程序输出命中文件、行号与摘录，并生成本地轨迹报告。当前只做关键词检索，不调用模型、不提供语义搜索，也不宣称已获得真实用户验证。
