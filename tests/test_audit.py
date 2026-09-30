import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_trace_lab.audit import MAX_LINE_BYTES, audit_trace, compare_traces, load_trace


def trace(error=False):
    data = [dict(type="run_start", name="demo", capture_values=False),
            dict(type="tool_start", call_id="a", parent_id=None, tool="search"),
            dict(type="tool_start", call_id="b", parent_id="a", tool="fetch"),
            dict(type="tool_end", call_id="b", tool="fetch", status="error" if error else "ok", duration_ms=2),
            dict(type="tool_end", call_id="a", tool="search", status="ok", duration_ms=5),
            dict(type="run_end", status="ok", duration_ms=8)]
    for seq, event in enumerate(data, 1):
        event.update(schema_version=1, run_id="run-1", seq=seq, timestamp="2026-09-30T12:00:00+00:00")
    return data


class AuditTests(unittest.TestCase):
    def test_valid_nested_summary_and_pairing(self):
        report = audit_trace(trace())
        self.assertTrue(report["passed"])
        self.assertEqual(report["summary"]["max_depth"], 2)
        self.assertEqual(report["summary"]["tool_calls"], 2)
        self.assertEqual(report["calls"][1]["end_seq"], 4)
        self.assertEqual(report["summary"]["tools"][0], dict(name="fetch", calls=1, errors=0, cancelled=0, duration_ms=2))

    def test_errors_are_measurements_until_budgeted(self):
        self.assertTrue(audit_trace(trace(True))["passed"])
        self.assertFalse(audit_trace(trace(True), max_errors=0)["passed"])
        self.assertTrue(audit_trace(trace(), max_calls=2, max_duration_ms=8)["passed"])
        self.assertFalse(audit_trace(trace(), max_calls=1, max_duration_ms=7)["passed"])
        events = trace(True)
        events[-1]["status"] = "error"
        self.assertIn("run_failed", [item["code"] for item in audit_trace(events)["issues"]])

    def test_detached_async_child_can_begin_after_parent_returns(self):
        events = trace()
        events[2], events[3], events[4] = events[4], events[2], events[3]
        for seq, event in enumerate(events, 1):
            event["seq"] = seq
        self.assertTrue(audit_trace(events)["passed"])

    def test_semantic_corruption_produces_issues(self):
        changes = [("duplicate_call_start", lambda x: x.insert(2, copy.deepcopy(x[1]))),
                   ("duplicate_call_end", lambda x: x.insert(4, copy.deepcopy(x[3]))),
                   ("orphan_call_end", lambda x: x[3].update(call_id="absent")),
                   ("tool_name_changed", lambda x: x[3].update(tool="other")),
                   ("missing_call_end", lambda x: x.pop(3)),
                   ("invalid_sequence", lambda x: x[2].update(seq=1)),
                   ("unresolved_parent", lambda x: x[2].update(parent_id="absent")),
                   ("parent_cycle", lambda x: x[1].update(parent_id="b")),
                   ("missing_run_end", lambda x: x.pop()),
                   ("missing_run_start", lambda x: x.pop(0)),
                   ("mixed_runs", lambda x: x[2].update(run_id="other")),
                   ("event_after_run_end", lambda x: x.append(copy.deepcopy(x[-1])))]
        for code, change in changes:
            with self.subTest(code=code):
                events = trace()
                change(events)
                report = audit_trace(events)
                self.assertFalse(report["passed"])
                self.assertIn(code, [issue["code"] for issue in report["issues"]])

    def test_invalid_structure_rejected_with_line(self):
        for key, value in (("schema_version", True), ("seq", True), ("timestamp", "yesterday"),
                           ("timestamp", "2026-09-30T12:00:00+01:00"), ("run_id", ""),
                           ("duration_ms", float("nan")), ("duration_ms", -1), ("status", "unknown")):
            with self.subTest(key=key, value=value):
                events = trace()
                events[3][key] = value
                with self.assertRaisesRegex(ValueError, "line 4"):
                    audit_trace(events)
        for events in ([], [None], [{}]):
            with self.assertRaises(ValueError):
                audit_trace(events)
        events = trace()
        del events[1]["parent_id"]
        with self.assertRaisesRegex(ValueError, "line 2"):
            audit_trace(events)

    def test_comparison_gates_counts_errors_and_optional_duration(self):
        before = audit_trace(trace())
        events = trace(True)
        events[-1]["duration_ms"] = 20
        after = audit_trace(events)
        self.assertFalse(compare_traces(before, after)["passed"])
        self.assertTrue(compare_traces(before, after, max_extra_errors=1)["passed"])
        self.assertFalse(compare_traces(before, after, max_extra_errors=1, max_duration_ratio=2)["passed"])
        self.assertEqual(compare_traces(before, after)["deltas"], dict(tool_calls=0, errors=1, cancelled=0, duration_ms=12))
        bad = audit_trace(trace()[:-1])
        self.assertIn("before_invalid", [x["code"] for x in compare_traces(bad, before)["issues"]])
        empty = trace()[::5]
        empty[1]["seq"] = 2
        fewer = audit_trace(empty)
        self.assertFalse(compare_traces(fewer, before)["passed"])
        self.assertTrue(compare_traces(fewer, before, max_extra_calls=2)["passed"])

    def test_invalid_budgets_and_zero_duration_baseline(self):
        for value in (-1, True, float("inf"), "2"):
            with self.assertRaises(ValueError):
                audit_trace(trace(), max_calls=value)
        before = audit_trace(trace())
        after = copy.deepcopy(before)
        before["summary"]["duration_ms"] = 0
        self.assertFalse(compare_traces(before, after, max_duration_ratio=2)["passed"])
        with self.assertRaises(ValueError):
            compare_traces(before, after, max_extra_errors=0.5)

    def test_duration_and_ratio_integers_must_fit_floating_point(self):
        huge = 10 ** 400
        for index in (3, 5):
            events = trace()
            events[index]["duration_ms"] = huge
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "line .*floating-point range"):
                audit_trace(events)
        with self.assertRaisesRegex(ValueError, "max_duration_ms.*floating-point range"):
            audit_trace(trace(), max_duration_ms=huge)
        report = audit_trace(trace())
        with self.assertRaisesRegex(ValueError, "max_duration_ratio.*floating-point range"):
            compare_traces(report, report, max_duration_ratio=huge)
        # Integer budgets do not undergo a lossy floating-point conversion.
        self.assertTrue(audit_trace(trace(), max_calls=huge, max_errors=huge)["passed"])
        self.assertTrue(compare_traces(report, report, max_extra_calls=huge)["passed"])

    def test_same_tool_duration_aggregation_rejects_overflow(self):
        for duration in (1e308, 10 ** 308):
            events = trace()
            events[2]["tool"] = events[3]["tool"] = "search"
            events[3]["duration_ms"] = events[4]["duration_ms"] = duration
            with self.subTest(duration=duration), self.assertRaisesRegex(ValueError, "aggregate duration_ms.*floating-point range"):
                audit_trace(events)

    def test_large_representable_durations_remain_valid(self):
        events = trace()
        events[3]["duration_ms"] = 10 ** 308
        events[-1]["duration_ms"] = 1e308
        report = audit_trace(events)
        self.assertTrue(report["passed"])
        json.dumps(report, allow_nan=False)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "trace.jsonl"

    def test_round_trip_and_strict_input(self):
        self.path.write_text("\n".join(json.dumps(event) for event in trace()), encoding="utf-8")
        self.assertEqual(load_trace(self.path), trace())
        for bad in (b"", b"\n", b"{}\n", b"[]\n", b"{broken}\n", b"\xff\n",
                    b'{"x": 1, "x": 2}\n'):
            self.path.write_bytes(bad)
            with self.subTest(data=bad), self.assertRaisesRegex(ValueError, "line 1"):
                load_trace(self.path)
        event = trace()[0]
        event["values"] = [float("nan")]
        self.path.write_text(json.dumps(event), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            load_trace(self.path)

    def test_line_and_total_size_boundaries(self):
        event = trace()[0]
        event["padding"] = ""
        raw = json.dumps(event).encode("utf-8") + b"\n"
        event["padding"] = "x" * (MAX_LINE_BYTES - len(raw))
        raw = json.dumps(event).encode("utf-8") + b"\n"
        self.assertEqual(len(raw), MAX_LINE_BYTES)
        self.path.write_bytes(raw)
        self.assertEqual(len(load_trace(self.path)), 1)
        self.path.write_bytes(raw[:-1] + b" \n")
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            load_trace(self.path)
        raw = json.dumps(trace()[0]).encode("utf-8") + b"\n"
        with patch("agent_trace_lab.audit.MAX_TRACE_BYTES", len(raw) * 2):
            self.path.write_bytes(raw * 2)
            self.assertEqual(len(load_trace(self.path)), 2)
            self.path.write_bytes(raw * 3)
            with self.assertRaisesRegex(ValueError, "line 3: trace exceeds"):
                load_trace(self.path)


if __name__ == "__main__":
    unittest.main()
