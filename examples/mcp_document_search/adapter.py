"""The essential boundary: a successful RPC can contain a failed MCP tool."""

from mcp import Client


class MCPToolError(RuntimeError):
    """The server returned isError=true, rather than a JSON-RPC exception."""


class MCPResultError(RuntimeError):
    """The example server returned an unexpected success payload."""


async def call_document_tool(client: Client, tool: str, arguments: dict) -> dict:
    result = await client.call_tool(tool, arguments)
    if result.is_error:
        # Keep untrusted/server-specific message contents out of exception text.
        # Raising *inside* the traced function lets TraceSession record failure.
        raise MCPToolError(f"MCP tool {tool!r} returned isError=true")
    if not isinstance(result.structured_content, dict):
        raise MCPResultError("Expected structured JSON object from document server")
    return result.structured_content
