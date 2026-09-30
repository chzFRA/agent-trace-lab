"""Validate recorded traces and compare measured runs without executing them."""

import json
import math
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

MAX_LINE_BYTES = 1024 * 1024
MAX_TRACE_BYTES = 20 * 1024 * 1024
_REQUIRED = {
    "run_start": {"name": str, "capture_values": bool},
    "tool_start": {"call_id": str, "tool": str},
    "tool_end": {"call_id": str, "tool": str, "status": str},
    "run_end": {"status": str},
}


def _finite_json(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _finite_json(item)
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for item in value.values():
            _finite_json(item)
        return
    raise ValueError("values must be finite JSON data")


def _number(value, name, integer=False):
    if type(value) not in ((int,) if integer else (int, float)):
        raise ValueError("{} must be a nonnegative {}".format(name, "integer" if integer else "number"))
    if value < 0:
        raise ValueError("{} must be nonnegative".format(name))
    if not integer:
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("{} must be finite within floating-point range".format(name))


def _validate(event, line):
    try:
        if not isinstance(event, dict):
            raise ValueError("event must be an object")
        _finite_json(event)
        if type(event.get("schema_version")) is not int or event["schema_version"] != 1:
            raise ValueError("schema_version must be 1")
        kind = event.get("type")
        if not isinstance(kind, str) or kind not in _REQUIRED:
            raise ValueError("unknown event type")
        for key, cls in dict(_REQUIRED[kind], run_id=str, timestamp=str, seq=int).items():
            if type(event.get(key)) is not cls or (cls is str and not event[key]):
                raise ValueError("{} must be a nonempty {}".format(key, cls.__name__))
        if event["seq"] < 1:
            raise ValueError("seq must be positive")
        timestamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        if timestamp.utcoffset() != timedelta(0):
            raise ValueError("timestamp must include the UTC offset")
        if kind == "tool_start":
            if "parent_id" not in event or (event["parent_id"] is not None and
                                            (type(event["parent_id"]) is not str or not event["parent_id"])):
                raise ValueError("parent_id must be null or a nonempty string")
        if kind in ("tool_end", "run_end"):
            allowed = ("ok", "error", "cancelled") if kind == "tool_end" else ("ok", "error")
            if event["status"] not in allowed:
                raise ValueError("invalid status")
            _number(event.get("duration_ms"), "duration_ms")
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise ValueError("line {}: {}".format(line, exc)) from exc


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: {}".format(key))
        result[key] = value
    return result


def load_trace(path):
    """Read one UTF-8 JSONL run, rejecting malformed or oversized input."""
    events, total = [], 0
    with Path(path).open("rb") as stream:
        while True:
            raw = stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            line = len(events) + 1
            total += len(raw)
            if len(raw) > MAX_LINE_BYTES:
                raise ValueError("line {}: exceeds 1 MiB limit".format(line))
            if total > MAX_TRACE_BYTES:
                raise ValueError("line {}: trace exceeds 20 MiB limit".format(line))
            try:
                event = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise ValueError("line {}: invalid JSON: {}".format(line, exc)) from exc
            _validate(event, line)
            events.append(event)
    if not events:
        raise ValueError("line 1: trace is empty")
    return events


def audit_trace(events, *, max_calls=None, max_errors=None, max_duration_ms=None):
    """Audit structure, failed runs, and optional budgets.

    Handled tool errors are measurements until an explicit error budget is set.
    """
    for value, name, integer in ((max_calls, "max_calls", True), (max_errors, "max_errors", True),
                                  (max_duration_ms, "max_duration_ms", False)):
        if value is not None:
            _number(value, name, integer)
    if not isinstance(events, (list, tuple)) or not events:
        raise ValueError("line 1: events must be a nonempty sequence")
    for line, event in enumerate(events, 1):
        _validate(event, line)
    issues, calls, starts, ends = [], {}, [], []

    def issue(code, message, call_id=None):
        item = {"code": code, "severity": "error", "message": message}
        if call_id is not None:
            item["call_id"] = call_id
        issues.append(item)

    run_id, previous = events[0]["run_id"], 0
    for event in events:
        kind, seq = event["type"], event["seq"]
        if seq != previous + 1:
            issue("invalid_sequence", "Expected seq {}, got {}.".format(previous + 1, seq))
        previous = seq
        if event["run_id"] != run_id:
            issue("mixed_runs", "Each trace file must contain exactly one run_id.")
        if ends:
            issue("event_after_run_end", "An event occurs after run_end.")
        if kind == "run_start":
            starts.append(event)
            if len(starts) > 1 or seq != 1:
                issue("invalid_run_start", "run_start must occur exactly once as the first event.")
        elif kind == "run_end":
            ends.append(event)
        elif kind == "tool_start":
            call_id = event["call_id"]
            if call_id in calls:
                issue("duplicate_call_start", "call_id was started more than once.", call_id)
                continue
            call = {key: event[key] for key in ("call_id", "parent_id", "tool")}
            call.update(status="incomplete", duration_ms=0, start_seq=seq)
            if "arguments" in event:
                call["arguments"] = event["arguments"]
            calls[call_id] = call
        else:
            call_id = event["call_id"]
            call = calls.get(call_id)
            if call is None:
                issue("orphan_call_end", "tool_end has no preceding tool_start.", call_id)
            elif "end_seq" in call:
                issue("duplicate_call_end", "call_id was ended more than once.", call_id)
            else:
                if call["tool"] != event["tool"]:
                    issue("tool_name_changed", "Tool name differs between start and end.", call_id)
                call.update(status=event["status"], duration_ms=event["duration_ms"], end_seq=seq)
                for key in ("result", "error"):
                    if key in event:
                        call[key] = event[key]
    if not starts:
        issue("missing_run_start", "Trace has no run_start.")
    if not ends:
        issue("missing_run_end", "Trace has no run_end; capture may be incomplete.")
    elif ends[0]["status"] == "error":
        issue("run_failed", "The recorded application run ended with an error.")
    for call_id, call in calls.items():
        if "end_seq" not in call:
            issue("missing_call_end", "Tool call has no tool_end.", call_id)
        parent = calls.get(call["parent_id"])
        if call["parent_id"] is not None and parent is None:
            issue("unresolved_parent", "parent_id does not identify a recorded tool call.", call_id)
        elif parent is not None:
            if parent["start_seq"] >= call["start_seq"]:
                issue("invalid_parent_order", "Parent must start before its child.", call_id)

    # Iterative, memoized parent walks also handle malicious cycles and deep trees.
    depths = {}
    for call_id in calls:
        path, seen, current = [], set(), call_id
        while current in calls and current not in depths and current not in seen:
            path.append(current)
            seen.add(current)
            current = calls[current]["parent_id"]
        if current in seen:
            issue("parent_cycle", "Tool parent links contain a cycle.", current)
            for member in path:
                depths[member] = 0
        else:
            depth = depths.get(current, 0)
            for member in reversed(path):
                depth += 1
                depths[member] = depth
    tools = {}
    counts = Counter(call["status"] for call in calls.values())
    for call_id, call in calls.items():
        call["depth"] = depths[call_id]
        tool = tools.setdefault(call["tool"], dict(name=call["tool"], calls=0, errors=0, duration_ms=0))
        tool["calls"] += 1
        tool["errors"] += call["status"] == "error"
        tool["duration_ms"] += call["duration_ms"]
        _number(tool["duration_ms"], "aggregate duration_ms for tool {!r}".format(call["tool"]))
    summary = dict(run_id=run_id, name=starts[0]["name"] if starts else "Unknown run",
                   status=ends[0]["status"] if ends else "incomplete", tool_calls=len(calls),
                   errors=counts["error"], cancelled=counts["cancelled"],
                   duration_ms=ends[0]["duration_ms"] if ends else 0,
                   max_depth=max(depths.values(), default=0), tools=[tools[name] for name in sorted(tools)])
    for value, key, code in ((max_calls, "tool_calls", "max_calls_exceeded"),
                             (max_errors, "errors", "max_errors_exceeded"),
                             (max_duration_ms, "duration_ms", "max_duration_exceeded")):
        if value is not None and summary[key] > value:
            issue(code, "{} {} exceeds budget {}.".format(key, summary[key], value))
    return dict(schema_version=1, summary=summary, calls=list(calls.values()), issues=issues,
                passed=not any(item["severity"] == "error" for item in issues))


def compare_traces(before_report, after_report, *, max_extra_calls=0, max_extra_errors=0,
                   max_duration_ratio=None):
    """Compare recorded measurements. Duration gates are opt-in and do not infer cause."""
    _number(max_extra_calls, "max_extra_calls", True)
    _number(max_extra_errors, "max_extra_errors", True)
    if max_duration_ratio is not None:
        _number(max_duration_ratio, "max_duration_ratio")
    for report in (before_report, after_report):
        if not isinstance(report, dict) or type(report.get("passed")) is not bool or not isinstance(report.get("summary"), dict):
            raise ValueError("comparison inputs must be audit reports")
        for key in ("tool_calls", "errors", "duration_ms"):
            _number(report["summary"].get(key), key, key != "duration_ms")
    before, after = before_report["summary"], after_report["summary"]
    deltas = {key: after[key] - before[key] for key in ("tool_calls", "errors", "duration_ms")}
    issues = []
    for label, report in (("before", before_report), ("after", after_report)):
        if not report["passed"]:
            issues.append(dict(code=label + "_invalid", severity="error", message=label + " run did not pass its audit."))
    for key, allowed in (("tool_calls", max_extra_calls), ("errors", max_extra_errors)):
        if deltas[key] > allowed:
            issues.append(dict(code=key + "_regression", severity="error",
                               message="{} increased by {}; allowed {}.".format(key, deltas[key], allowed)))
    if max_duration_ratio is not None and after["duration_ms"] > before["duration_ms"] * max_duration_ratio:
        issues.append(dict(code="duration_regression", severity="error",
                           message="Measured duration exceeds the configured ratio; timing alone does not establish cause."))
    return dict(schema_version=1, before=before, after=after, deltas=deltas, issues=issues, passed=not issues)
