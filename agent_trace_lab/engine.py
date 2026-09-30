"""Small tool executor with explicit budgets and observable failure semantics."""

import copy
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional


class TransientToolError(Exception):
    """A retryable tool failure, including synthetic injected failures."""


class DocumentNotFoundError(Exception):
    """The requested document does not exist in the synthetic corpus."""


@dataclass(frozen=True)
class Tool:
    name: str
    parameters: Mapping[str, type]
    handler: Callable[..., Any]


DOCUMENTS = {
    "deployment": {
        "title": "Synthetic deployment handbook",
        "paragraphs": [
            "Before deployment, run the test suite and create a rollback checkpoint.",
            "After deployment, inspect the health endpoint and error rate.",
        ],
    },
    "recovery": {
        "title": "Synthetic recovery handbook",
        "paragraphs": [
            "For a failed deployment, restore the last verified checkpoint.",
            "Record the incident and verify recovery with a health check.",
        ],
    },
    "credentials": {
        "title": "Synthetic credentials handbook",
        "paragraphs": [
            "Keep credentials in environment variables and exclude secrets from version control.",
            "Use a dedicated test credential for integration tests.",
        ],
    },
}

_STOP_WORDS = {"a", "an", "and", "are", "be", "for", "how", "in", "is", "of", "on", "the", "to", "what", "with"}


def _tokens(value: str) -> set:
    return set(re.findall(r"[a-z0-9]+", value.lower())) - _STOP_WORDS


def search_docs(query: str) -> Dict[str, Any]:
    """Rank a tiny synthetic corpus by lexical overlap; this is not semantic search."""
    query_tokens = _tokens(query)
    matches = []
    for document_id, document in DOCUMENTS.items():
        text = " ".join(document["paragraphs"])
        score = len(query_tokens & _tokens(document["title"] + " " + text))
        if score:
            matches.append({"document_id": document_id, "title": document["title"], "score": score})
    matches.sort(key=lambda item: (-item["score"], item["document_id"]))
    return {"matches": matches, "search_method": "lexical_token_overlap"}


def read_document(document_id: str) -> Dict[str, Any]:
    """Return text and stable paragraph citations from the synthetic corpus."""
    if document_id not in DOCUMENTS:
        raise DocumentNotFoundError("No document with id {!r}".format(document_id))
    document = DOCUMENTS[document_id]
    return {
        "document_id": document_id,
        "title": document["title"],
        "evidence": [
            {"citation_id": "doc:{}#p{}".format(document_id, index), "text": paragraph}
            for index, paragraph in enumerate(document["paragraphs"], start=1)
        ],
    }


def default_tools() -> Dict[str, Tool]:
    return {
        "search_docs": Tool("search_docs", {"query": str}, search_docs),
        "read_document": Tool("read_document", {"document_id": str}, read_document),
    }


class ToolExecutor:
    """Execute named tools once per call ID, recording every request.

    Each unique request admitted under the budget consumes one call, even if
    its tool or arguments are invalid. Duplicate IDs and over-budget requests
    do not consume another call. IDs are reserved only when a request is
    admitted. Retry attempts therefore need new IDs. Fault injection happens
    before handler dispatch and does not simulate elapsed wall-clock time.
    """

    def __init__(self, max_calls: int = 4, tools: Optional[Mapping[str, Tool]] = None,
                 transient_failures: Optional[Mapping[str, int]] = None,
                 scenario_id: str = "manual") -> None:
        if type(max_calls) is not int or not 1 <= max_calls <= 100:
            raise ValueError("max_calls must be an integer from 1 to 100")
        self.tools = dict(default_tools() if tools is None else tools)
        self.max_calls = max_calls
        self.scenario_id = scenario_id
        self.call_count = 0
        self.dispatch_count = 0
        self.events: List[Dict[str, Any]] = []
        self._seen = set()
        self._faults = dict(transient_failures or {})
        for name, count in self._faults.items():
            if name not in self.tools or type(count) is not int or not 0 <= count <= 100:
                raise ValueError("transient_failures requires known tools and integer counts from 0 to 100")

    def execute(self, call_id: str, tool_name: str, arguments: Any) -> Dict[str, Any]:
        if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > 200:
            raise ValueError("call_id must be a non-empty string of at most 200 characters")
        event = {
            "scenario_id": self.scenario_id,
            "sequence": len(self.events) + 1,
            "policy": "scripted",
            "call_id": call_id,
            "tool": tool_name,
            "arguments": copy.deepcopy(arguments),
            "admitted": False,
            "dispatched": False,
            "injected_failure": False,
        }
        if call_id in self._seen:
            return self._finish(event, "duplicate_call_id", error="Call ID was already admitted; handler was not executed again.")
        if self.call_count >= self.max_calls:
            return self._finish(event, "budget_exhausted", error="The maximum admitted-call budget was reached.")
        self._seen.add(call_id)
        self.call_count += 1
        event["admitted"] = True
        if not isinstance(tool_name, str) or tool_name not in self.tools:
            return self._finish(event, "unknown_tool", error="Tool is not registered.")
        tool = self.tools[tool_name]
        error = self._validate(tool, arguments)
        if error:
            return self._finish(event, "invalid_arguments", error=error)
        try:
            if self._faults.get(tool_name, 0):
                self._faults[tool_name] -= 1
                event["injected_failure"] = True
                raise TransientToolError("Injected transient timeout exception; no real-time deadline was measured.")
            self.dispatch_count += 1
            event["dispatched"] = True
            result = tool.handler(**copy.deepcopy(arguments))
        except TransientToolError as exc:
            return self._finish(event, "transient_error", error=str(exc))
        except DocumentNotFoundError as exc:
            return self._finish(event, "not_found", error=str(exc))
        except Exception as exc:
            return self._finish(event, "tool_error", error="{}: {}".format(type(exc).__name__, str(exc)))
        return self._finish(event, "success", result=result)

    @staticmethod
    def _validate(tool: Tool, arguments: Any) -> Optional[str]:
        if not isinstance(arguments, dict):
            return "Arguments must be an object."
        if set(arguments) != set(tool.parameters):
            return "Expected exactly these arguments: {}.".format(", ".join(sorted(tool.parameters)))
        for name, expected_type in tool.parameters.items():
            value = arguments[name]
            if type(value) is not expected_type:
                return "Argument {!r} must have type {}.".format(name, expected_type.__name__)
            if isinstance(value, str) and (not value.strip() or len(value) > 2000):
                return "Argument {!r} must contain 1 to 2000 characters and cannot be blank.".format(name)
        return None

    def _finish(self, event: Dict[str, Any], status: str, **payload: Any) -> Dict[str, Any]:
        event.update(status=status, calls_used=self.call_count, max_calls=self.max_calls, **copy.deepcopy(payload))
        self.events.append(copy.deepcopy(event))
        return copy.deepcopy(event)
