"""Release regressions derived from independently reproduced failure cases."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_trace_lab.audit import audit_trace, compare_traces, load_trace


def recorded_run(tools, capture_values=False):
    events = [dict(type="run_start", name="same-task", capture_values=capture_values)]
    for index, (name, status) in enumerate(tools):
        events.extend([dict(type="tool_start", call_id=str(index), parent_id=None, tool=name),
                       dict(type="tool_end", call_id=str(index), tool=name, status=status, duration_ms=1)])
    events.append(dict(type="run_end", status="ok", duration_ms=10))
    for seq, event in enumerate(events, 1):
        event.update(schema_version=1, seq=seq, run_id="example", timestamp="2026-10-01T00:00:00+00:00")
    return events


class ReleaseAuditTests(unittest.TestCase):
    def test_swapped_errors_are_visible_and_opt_in_gate_catches_them(self):
        before = audit_trace(recorded_run([("retrieve_customer", "error"), ("charge_customer", "ok")]))
        after = audit_trace(recorded_run([("retrieve_customer", "ok"), ("charge_customer", "error")]))
        comparison = compare_traces(before, after)
        self.assertTrue(comparison["passed"])
        self.assertIs(comparison["per_tool"], False)
        self.assertEqual(comparison["deltas"]["errors"], 0)
        tools = {tool["name"]: tool for tool in comparison["per_tool_deltas"]}
        self.assertEqual(tools["charge_customer"]["deltas"]["errors"], 1)
        self.assertEqual(tools["retrieve_customer"]["deltas"]["errors"], -1)
        gated = compare_traces(before, after, per_tool=True)
        self.assertFalse(gated["passed"])
        self.assertIs(gated["per_tool"], True)
        self.assertEqual(gated["issues"][0]["tool"], "charge_customer")
        self.assertTrue(compare_traces(before, after, per_tool=True, max_extra_errors=1)["passed"])

    def test_replaced_tool_has_explicit_zeroes_and_same_global_count(self):
        before = audit_trace(recorded_run([("old_tool", "ok")]))
        after = audit_trace(recorded_run([("new_tool", "ok")]))
        comparison = compare_traces(before, after, per_tool=True)
        self.assertEqual(comparison["deltas"]["tool_calls"], 0)
        self.assertFalse(comparison["passed"])
        new, old = comparison["per_tool_deltas"]
        self.assertEqual(new["name"], "new_tool")
        self.assertEqual(new["before"], dict(calls=0, errors=0, cancelled=0, duration_ms=0))
        self.assertEqual(old["after"], dict(calls=0, errors=0, cancelled=0, duration_ms=0))
        self.assertTrue(compare_traces(before, after, per_tool=True, max_extra_calls=1)["passed"])

    def test_cancellation_is_counted_budgeted_and_compared(self):
        before = audit_trace(recorded_run([("retrieve", "ok")]))
        events = recorded_run([("retrieve", "cancelled")])
        after = audit_trace(events)
        self.assertTrue(after["passed"])
        self.assertEqual(after["summary"]["tools"][0]["cancelled"], 1)
        self.assertFalse(audit_trace(events, max_cancelled=0)["passed"])
        self.assertTrue(audit_trace(events, max_cancelled=1)["passed"])
        comparison = compare_traces(before, after)
        self.assertFalse(comparison["passed"])
        self.assertEqual(comparison["deltas"]["cancelled"], 1)
        self.assertTrue(compare_traces(before, after, max_extra_cancelled=1, per_tool=True)["passed"])

    def test_swapped_cancellations_are_caught_per_tool(self):
        before = audit_trace(recorded_run([("a", "cancelled"), ("b", "ok")]))
        after = audit_trace(recorded_run([("a", "ok"), ("b", "cancelled")]))
        self.assertTrue(compare_traces(before, after)["passed"])
        comparison = compare_traces(before, after, per_tool=True)
        self.assertFalse(comparison["passed"])
        self.assertIn("per_tool_cancelled_regression", [issue["code"] for issue in comparison["issues"]])

    def test_invalid_new_options_are_rejected(self):
        report = audit_trace(recorded_run([]))
        for value in (True, -1, 0.5, "0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit_trace(recorded_run([]), max_cancelled=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                compare_traces(report, report, max_extra_cancelled=value)
        with self.assertRaisesRegex(ValueError, "per_tool must be a boolean"):
            compare_traces(report, report, per_tool="false")

    def test_impossible_tool_duration_cannot_evade_run_budget(self):
        events = recorded_run([("slow", "ok")])
        events[2]["duration_ms"] = 100000
        events[-1]["duration_ms"] = 0
        report = audit_trace(events, max_duration_ms=1)
        self.assertFalse(report["passed"])
        self.assertIn("tool_duration_exceeds_run", [issue["code"] for issue in report["issues"]])

    def test_concurrent_or_nested_durations_are_not_added_for_run_invariant(self):
        events = recorded_run([("retrieve", "ok"), ("retrieve", "ok")])
        events[2], events[3] = events[3], events[2]
        events[3]["duration_ms"] = events[4]["duration_ms"] = 8
        for seq, event in enumerate(events, 1):
            event["seq"] = seq
        for nested in (False, True):
            with self.subTest(nested=nested):
                events[2]["parent_id"] = "0" if nested else None
                report = audit_trace(events)
                self.assertTrue(report["passed"])
                self.assertEqual(report["summary"]["tools"][0]["duration_ms"], 16)

    def test_disabled_capture_cannot_claim_captured_payloads_are_private(self):
        for event_index, key, value in ((1, "arguments", {"query": "private"}),
                                         (2, "result", "private"),
                                         (2, "error", {"type": "ValueError", "message": "private"})):
            events = recorded_run([("retrieve", "error" if key == "error" else "ok")])
            events[event_index][key] = value
            with self.subTest(key=key):
                report = audit_trace(events)
                self.assertFalse(report["passed"])
                self.assertIn("capture_policy_mismatch", [issue["code"] for issue in report["issues"]])
                events[0]["capture_values"] = True
                self.assertTrue(audit_trace(events)["passed"])
        events = recorded_run([("retrieve", "error")])
        events[2]["error"] = {"type": "ValueError"}
        self.assertTrue(audit_trace(events)["passed"])

    def test_lone_surrogates_are_rejected_in_json_keys_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            for payload in ({"field": "bad\ud800name"}, {"bad\udfffkey": "value"}):
                events = recorded_run([("retrieve", "ok")], capture_values=True)
                events[1]["arguments"] = payload
                path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
                with self.subTest(payload=repr(payload)), self.assertRaisesRegex(ValueError, "line 2:.*surrogate"):
                    load_trace(path)

    def test_json_nesting_limit_prevents_pretty_print_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deep.jsonl"
            for depth in (31, 32, 200):
                value = 0
                for _ in range(depth):
                    value = [value]
                events = recorded_run([("retrieve", "ok")], capture_values=True)
                events[1]["arguments"] = value
                path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
                with self.subTest(depth=depth):
                    if depth == 31:  # The event object is the remaining container level.
                        self.assertTrue(audit_trace(load_trace(path))["passed"])
                    else:
                        with self.assertRaisesRegex(ValueError, "line 2: JSON nesting exceeds 32 levels"):
                            load_trace(path)

    def test_old_per_tool_summaries_default_missing_cancelled_to_zero(self):
        report = audit_trace(recorded_run([("retrieve", "ok")]))
        old = copy.deepcopy(report)
        del old["summary"]["tools"][0]["cancelled"]
        self.assertTrue(compare_traces(old, report, per_tool=True)["passed"])


if __name__ == "__main__":
    unittest.main()
