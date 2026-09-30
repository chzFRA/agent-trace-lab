"""Bounded, declarative scenarios. No scenario can supply executable code."""

import json
import math
from pathlib import Path
from typing import Any, Dict, List


def builtin_scenarios() -> List[Dict[str, Any]]:
    return [
        {"id": "grounded_answer", "description": "Retrieve a deployment paragraph and preserve its citation.",
         "mode": "retrieve", "query": "deployment test suite rollback checkpoint", "max_calls": 4,
         "expect": {"outcome": "answered", "statuses": ["success"], "citations": ["doc:deployment#p1"]}},
        {"id": "transient_recovery", "description": "Recover from one injected transient timeout exception.",
         "mode": "retrieve", "query": "deployment test suite rollback checkpoint", "max_calls": 4,
         "max_retries": 1, "transient_failures": {"read_document": 1},
         "expect": {"outcome": "answered", "statuses": ["transient_error", "success"], "citations": ["doc:deployment#p1"]}},
        {"id": "missing_evidence", "description": "Abstain when lexical search returns no evidence.",
         "mode": "retrieve", "query": "orbital banana telescope", "max_calls": 4,
         "expect": {"outcome": "abstained", "statuses": ["success"], "citations": []}},
        {"id": "invalid_arguments", "description": "Reject a non-string query before handler dispatch.",
         "mode": "probe", "max_calls": 2,
         "calls": [{"call_id": "invalid-1", "tool": "search_docs", "arguments": {"query": 123}}],
         "expect": {"outcome": "rejected", "statuses": ["invalid_arguments"]}},
        {"id": "unknown_tool", "description": "Reject an unregistered tool before dispatch.",
         "mode": "probe", "max_calls": 2,
         "calls": [{"call_id": "unknown-1", "tool": "send_email", "arguments": {"message": "synthetic"}}],
         "expect": {"outcome": "rejected", "statuses": ["unknown_tool"]}},
        {"id": "budget_exhausted", "description": "Stop after search when the call budget cannot admit document reading.",
         "mode": "retrieve", "query": "deployment test suite rollback checkpoint", "max_calls": 1,
         "expect": {"outcome": "budget_exhausted", "statuses": ["success", "budget_exhausted"], "citations": []}},
        {"id": "duplicate_call_id", "description": "Reject a repeated call ID without executing the handler again.",
         "mode": "probe", "max_calls": 3,
         "calls": [
             {"call_id": "same-id", "tool": "read_document", "arguments": {"document_id": "deployment"}},
             {"call_id": "same-id", "tool": "read_document", "arguments": {"document_id": "recovery"}},
         ],
         "expect": {"outcome": "rejected", "statuses": ["success", "duplicate_call_id"]}},
        {"id": "retry_limit", "description": "Stop after the bounded retry is also interrupted by an injected exception.",
         "mode": "retrieve", "query": "deployment test suite rollback checkpoint", "max_calls": 4,
         "max_retries": 1, "transient_failures": {"read_document": 2},
         "expect": {"outcome": "failed", "statuses": ["success", "transient_error"], "citations": []}},
    ]


def _validate_finite_numbers(value: Any, depth: int = 0) -> None:
    if depth > 50:
        raise ValueError("Scenario nesting must not exceed 50 levels.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Scenario numbers must be finite; NaN and infinity are not valid JSON values.")
    if isinstance(value, dict):
        for item in value.values():
            _validate_finite_numbers(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_finite_numbers(item, depth + 1)


def _reject_constant(value: str) -> None:
    raise ValueError("Non-standard JSON number {!r} is not allowed.".format(value))


def validate_scenario(raw: Any) -> Dict[str, Any]:
    _validate_finite_numbers(raw)
    if not isinstance(raw, dict):
        raise ValueError("Scenario must be a JSON object.")
    allowed = {"id", "description", "mode", "query", "max_calls", "max_retries", "transient_failures", "calls", "expect"}
    if set(raw) - allowed:
        raise ValueError("Unknown scenario fields: {}".format(", ".join(sorted(set(raw) - allowed))))
    scenario = dict(raw)
    for name in ("id", "description"):
        value = scenario.get(name, "" if name == "id" else "Custom synthetic scenario")
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError("{} must be a non-empty string of at most 2000 characters.".format(name))
        scenario[name] = value
    if scenario.get("mode") not in ("retrieve", "probe"):
        raise ValueError("mode must be 'retrieve' or 'probe'.")
    for name, default, maximum in (("max_calls", 4, 100), ("max_retries", 1, 10)):
        value = scenario.get(name, default)
        minimum = 1 if name == "max_calls" else 0
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError("{} must be an integer from {} to {}.".format(name, minimum, maximum))
        scenario[name] = value
    faults = scenario.get("transient_failures", {})
    if not isinstance(faults, dict) or any(
        name not in ("search_docs", "read_document") or type(count) is not int or not 0 <= count <= 100
        for name, count in faults.items()
    ):
        raise ValueError("transient_failures must map registered tool names to integers from 0 to 100.")
    scenario["transient_failures"] = dict(faults)
    if scenario["mode"] == "retrieve":
        query = scenario.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError("retrieve mode requires a non-empty query of at most 2000 characters.")
        if "calls" in scenario:
            raise ValueError("retrieve mode does not accept calls.")
    else:
        calls = scenario.get("calls")
        if not isinstance(calls, list) or not 1 <= len(calls) <= 100:
            raise ValueError("probe mode requires 1 to 100 calls.")
        for call in calls:
            if not isinstance(call, dict) or set(call) != {"call_id", "tool", "arguments"}:
                raise ValueError("Each call requires exactly call_id, tool, and arguments.")
            if not isinstance(call["call_id"], str) or not call["call_id"].strip() or len(call["call_id"]) > 200:
                raise ValueError("Each call_id must be a non-empty string of at most 200 characters.")
            if not isinstance(call["tool"], str) or len(call["tool"]) > 200:
                raise ValueError("Each tool name must be a string of at most 200 characters.")
        if "query" in scenario:
            raise ValueError("probe mode does not accept query.")
    expected = scenario.get("expect", {})
    if not isinstance(expected, dict) or set(expected) - {"outcome", "statuses", "citations"}:
        raise ValueError("expect accepts only outcome, statuses, and citations.")
    if "outcome" in expected and expected["outcome"] not in {"answered", "abstained", "rejected", "budget_exhausted", "failed", "completed"}:
        raise ValueError("Unsupported expected outcome.")
    for name in ("statuses", "citations"):
        if name in expected and (not isinstance(expected[name], list) or not all(isinstance(item, str) for item in expected[name])):
            raise ValueError("expect.{} must be a list of strings.".format(name))
    scenario["expect"] = dict(expected)
    return scenario


def load_scenario(path: Path) -> Dict[str, Any]:
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Scenario file must not exceed 1 MiB.")
    return validate_scenario(json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant))
