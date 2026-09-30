"""Run a real stdio MCP integration or search an explicitly selected directory."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from mcp import Client, StdioServerParameters

EXAMPLE = Path(__file__).resolve().parent
REPOSITORY = EXAMPLE.parents[1]
sys.path.insert(0, str(REPOSITORY))

from agent_trace_lab import TraceSession, __version__  # noqa: E402
from agent_trace_lab.audit import audit_trace, compare_traces, load_trace  # noqa: E402
from agent_trace_lab.inspector import write_inspection  # noqa: E402
from adapter import MCPToolError, call_document_tool  # noqa: E402


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@asynccontextmanager
async def connection(root: Path):
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(EXAMPLE / "server.py"), "--root", str(root)])
    # A subprocess over stdio, not an in-process server or a mocked client.
    async with Client(params, read_timeout_seconds=10) as client:
        yield client


def instrument(client: Client, trace: TraceSession):
    @trace.tool("mcp.search_documents")
    async def search(query, limit=5):
        return await call_document_tool(client, "search_documents", {"query": query, "limit": limit})

    @trace.tool("mcp.read_document")
    async def read(path):
        return await call_document_tool(client, "read_document", {"path": path})

    return search, read


def report(output: Path, name: str, **budgets):
    result = audit_trace(load_trace(output / f"{name}.jsonl"), **budgets)
    write_inspection(result, output / name)
    return result


async def verify(output: Path) -> dict:
    evidence = {"purpose": "Real local stdio integration; no model or external user validation",
                "environment": {"python": platform.python_version(), "mcp": version("mcp"),
                                "agent_trace_lab": __version__, "transport": "stdio subprocess"},
                "checks": []}
    completed = False

    def check(name, condition, observed):
        evidence["checks"].append({"name": name, "passed": bool(condition), "observed": observed})
        if not condition:
            raise AssertionError(f"Acceptance check failed: {name}")

    try:
        with tempfile.TemporaryDirectory(prefix="agenttrace-mcp-documents-") as temp:
            root = Path(temp) / "documents"
            shutil.copytree(EXAMPLE / "documents", root)
            async with connection(root) as client:
                evidence["environment"]["protocol_version"] = str(client.protocol_version)
                tools = await client.list_tools()
                schemas = {tool.name: tool.input_schema for tool in tools.tools}
                save_json(output / "discovered-tools.json", schemas)
                check("real MCP tool discovery", set(schemas) == {"search_documents", "read_document"},
                      sorted(schemas))
                check("query JSON schema", schemas["search_documents"]["properties"]["query"]["type"] == "string",
                      schemas["search_documents"]["properties"]["query"])

                with TraceSession(output / "baseline.jsonl", name="MCP baseline") as trace:
                    search, read = instrument(client, trace)
                    found = await search("retry attempts")
                    check("known query finds expected document", [x["path"] for x in found["matches"]] == ["reliability.md"],
                          found)
                    first = await read(found["matches"][0]["path"])
                    check("real document read returns expected evidence",
                          "Retry attempts should have an explicit budget" in first["text"], first["path"])
                check("baseline trace recording completed", not trace.logging_errors, list(trace.logging_errors))
                baseline = report(output, "baseline", max_calls=2, max_errors=0)
                check("baseline budget", baseline["passed"] and baseline["summary"]["tool_calls"] == 2,
                      baseline["summary"])

                with TraceSession(output / "repeated.jsonl", name="MCP repeated read") as trace:
                    search, read = instrument(client, trace)
                    found = await search("retry attempts")
                    first_read = await read(found["matches"][0]["path"])
                    second_read = await read(found["matches"][0]["path"])
                    check("duplicate read has same content", first_read == second_read, first_read["path"])
                repeated = report(output, "repeated", max_errors=0)
                comparison = compare_traces(baseline, repeated)
                write_inspection(comparison, output / "comparison", comparison=True)
                check("extra MCP call fails regression check", not comparison["passed"]
                      and comparison["deltas"]["tool_calls"] == 1
                      and any(x["code"] == "tool_calls_regression" for x in comparison["issues"]),
                      {"deltas": comparison["deltas"], "issues": comparison["issues"]})

                with TraceSession(output / "empty.jsonl", name="MCP empty search") as trace:
                    search, _ = instrument(client, trace)
                    empty = await search("unfindablequokkas")
                empty_report = report(output, "empty", max_calls=1, max_errors=0)
                check("empty results remain a successful tool call", empty["matches"] == []
                      and empty_report["passed"], empty)

                # Show the real semantic trap before demonstrating its repair.
                with TraceSession(output / "raw-error.jsonl", name="MCP raw error flag") as trace:
                    @trace.tool("mcp.read_document.raw")
                    async def raw_read():
                        return await client.call_tool("read_document", {"path": "missing.md"})

                    raw = await raw_read()
                raw_report = report(output, "raw-error", max_errors=0)
                wire_flag = raw.model_dump(by_alias=True)["isError"]
                check("server tool error crosses MCP as isError", wire_flag is True, {"isError": wire_flag})
                check("raw RPC wrapper misses semantic tool failure", raw_report["summary"]["errors"] == 0
                      and raw_report["passed"], {"trace_errors": raw_report["summary"]["errors"],
                                                "explanation": "RPC returned normally despite isError=true"})

                with TraceSession(output / "adapter-error.jsonl", name="MCP adapter observes error") as trace:
                    _, read = instrument(client, trace)
                    caught = False
                    try:
                        await read("missing.md")
                    except MCPToolError:
                        caught = True
                adapter_report = report(output, "adapter-error", max_errors=0)
                error_types = [x.get("error", {}).get("type") for x in adapter_report["calls"]]
                check("adapter makes tool error observable", caught and not adapter_report["passed"]
                      and adapter_report["summary"]["errors"] == 1 and error_types == ["MCPToolError"],
                      {"trace_errors": adapter_report["summary"]["errors"], "error_types": error_types})

                (Path(temp) / "outside.md").write_text("Outside the allowed root", encoding="utf-8")
                (root / "linked.md").symlink_to(Path(temp) / "outside.md")
                (root / "linked-directory").symlink_to(Path(temp), target_is_directory=True)
                (root / ".private.md").write_text("Hidden fixture, not a search document", encoding="utf-8")
                (root / "unsupported.json").write_text("{}", encoding="utf-8")
                (root / "oversized.md").write_bytes(b"x" * (128 * 1024 + 1))
                (root / "invalid-utf8.md").write_bytes(b"\xff\xfe")
                invalid = [("blank query", "search_documents", {"query": "   "}),
                           ("wrong query type", "search_documents", {"query": 12}),
                           ("oversized query", "search_documents", {"query": "a" * 241}),
                           ("invalid result limit", "search_documents", {"query": "retry", "limit": 11}),
                           ("parent traversal", "read_document", {"path": "../outside.md"}),
                           ("absolute path", "read_document", {"path": str(Path(temp) / "outside.md")}),
                           ("symlink escape", "read_document", {"path": "linked.md"}),
                           ("symlink ancestor", "read_document", {"path": "linked-directory/outside.md"}),
                           ("hidden document", "read_document", {"path": ".private.md"}),
                           ("unsupported file type", "read_document", {"path": "unsupported.json"}),
                           ("oversized document", "read_document", {"path": "oversized.md"}),
                           ("invalid UTF-8", "read_document", {"path": "invalid-utf8.md"})]
                with TraceSession(output / "rejections.jsonl", name="MCP rejected inputs") as trace:
                    @trace.tool("mcp.validation_probe")
                    async def rejected_call(tool, arguments):
                        return await call_document_tool(client, tool, arguments)

                    for name, tool, arguments in invalid:
                        rejected = False
                        try:
                            await rejected_call(tool, arguments)
                        except MCPToolError:
                            rejected = True
                        check(name + " rejected across MCP", rejected, "MCPToolError" if rejected else "unexpected success")
                rejected_report = report(output, "rejections")
                check("all rejected calls recorded", rejected_report["passed"]
                      and rejected_report["summary"]["errors"] == len(invalid),
                      {"calls": rejected_report["summary"]["tool_calls"], "errors": rejected_report["summary"]["errors"]})

        traces = sorted(output.glob("*.jsonl"))
        captured_values = [event for path in traces for event in load_trace(path)
                           if "arguments" in event or "result" in event
                           or "message" in event.get("error", {})]
        check("trace capture omits document contents and inputs", not captured_values,
              {"trace_files_checked": len(traces), "captured_value_events": len(captured_values)})
        command = [sys.executable, "-m", "agent_trace_lab", "compare", str(output / "baseline.jsonl"),
                   str(output / "repeated.jsonl"), "--output", str(output / "cli-comparison")]
        result = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True, timeout=30)
        check("CLI exposes regression to CI", result.returncode == 1,
              {"exit_code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()})
        check("reports include portable HTML", (output / "comparison" / "report.html").is_file()
              and (output / "adapter-error" / "report.html").is_file(),
              ["comparison/report.html", "adapter-error/report.html"])
        completed = True
    finally:
        evidence["passed"] = completed and bool(evidence["checks"]) and all(x["passed"] for x in evidence["checks"])
        save_json(output / "acceptance.json", evidence)
    return evidence


async def search_directory(root: Path, query: str, limit: int, output: Path) -> dict:
    async with connection(root) as client:
        with TraceSession(output / "search.jsonl", name="MCP document search") as trace:
            search, _ = instrument(client, trace)
            try:
                found = await search(query, limit)
            except MCPToolError:
                # Produce a useful error report while propagating the failure.
                found = None
    inspection = report(output, "search", max_errors=0)
    if found is None or not inspection["passed"]:
        raise MCPToolError("Search failed; inspect search/report.html and the server error on stderr")
    save_json(output / "search-results.json", found)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    acceptance = commands.add_parser("verify", help="Run fixed integration checks against bundled documents")
    acceptance.add_argument("--output", type=Path, default=Path("artifacts/mcp-acceptance"))
    search = commands.add_parser("search", help="Search non-sensitive documents in a specified directory")
    search.add_argument("query")
    search.add_argument("--root", type=Path, default=EXAMPLE / "documents")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--output", type=Path, default=Path("artifacts/mcp-search"))
    args = parser.parse_args()
    output = args.output.resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("Output directory already exists; choose a new directory to preserve recorded runs")
    if args.command == "verify":
        result = asyncio.run(verify(output))
        print(f"PASS: {len(result['checks'])} checks over a real MCP stdio subprocess")
        print(f"Evidence: {output / 'acceptance.json'}")
        print(f"Regression example: {output / 'comparison' / 'report.html'}")
    else:
        result = asyncio.run(search_directory(args.root.resolve(), args.query, args.limit, output))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"Trace report: {output / 'search' / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
