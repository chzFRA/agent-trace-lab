"""Read-only MCP tools over a bounded local Markdown/text directory."""

import argparse
from pathlib import Path
import re
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field


MAX_DOCUMENT_BYTES = 128 * 1024
MAX_DOCUMENTS = 128
SUFFIXES = {".md", ".txt"}


class Match(BaseModel):
    path: str
    line: int
    excerpt: str


class SearchResult(BaseModel):
    query: str
    matches: list[Match]
    matched_documents: int


class Document(BaseModel):
    path: str
    text: str


def make_server(root: Path) -> MCPServer:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Document root must be a directory")
    server = MCPServer("agenttrace-document-search", version="1.0", log_level="WARNING")

    def read_local(relative: str) -> str:
        path = Path(relative)
        if (not relative or path.is_absolute() or ".." in path.parts
                or any(part.startswith(".") for part in path.parts)
                or path.suffix.lower() not in SUFFIXES):
            raise ToolError("Use a relative .md or .txt path inside the document root")
        candidate = root.joinpath(path)
        if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != root):
            raise ToolError("Symbolic links are not documents in this example")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError, RuntimeError) as error:
            raise ToolError("Document is missing or outside the document root") from error
        if not resolved.is_file():
            raise ToolError("Document must be a regular file")
        try:
            with resolved.open("rb") as stream:
                data = stream.read(MAX_DOCUMENT_BYTES + 1)
            if len(data) > MAX_DOCUMENT_BYTES:
                raise ToolError("Document exceeds the 128 KiB example limit")
            return data.decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise ToolError("Document cannot be read as UTF-8 text") from error

    read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                idempotent_hint=True, open_world_hint=False)

    @server.tool(annotations=read_only)
    def search_documents(
        query: Annotated[str, Field(min_length=1, max_length=240, strict=True)],
        limit: Annotated[int, Field(ge=1, le=10, strict=True)] = 5,
    ) -> SearchResult:
        """Search lines containing all query words in the configured local documents."""
        words = re.findall(r"[^\W_]+", query.casefold())
        if not words or len(words) > 16:
            raise ToolError("Query must contain between 1 and 16 words")
        files = []
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if (path.suffix.lower() in SUFFIXES and path.is_file()
                    and not any(part.startswith(".") for part in relative.parts)):
                files.append(relative.as_posix())
                if len(files) > MAX_DOCUMENTS:
                    raise ToolError("Document root exceeds the 128-document example limit")
        matches = []
        for relative in sorted(files):
            for number, line in enumerate(read_local(relative).splitlines(), 1):
                folded = line.casefold()
                if all(word in folded for word in words):
                    matches.append({"path": relative, "line": number,
                                    "excerpt": line[:320]})
                    break
        return SearchResult(query=query, matches=matches[:limit], matched_documents=len(matches))

    @server.tool(annotations=read_only)
    def read_document(path: Annotated[str, Field(min_length=1, max_length=240, strict=True)]) -> Document:
        """Read one relative Markdown/text document from the configured root."""
        return Document(path=Path(path).as_posix(), text=read_local(path))

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent / "documents")
    args = parser.parse_args()
    make_server(args.root).run(transport="stdio")
