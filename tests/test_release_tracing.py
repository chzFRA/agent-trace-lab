"""Recorder failure-injection and lifecycle checks; no timing thresholds."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import warnings

from agent_trace_lab.audit import audit_trace, load_trace
from agent_trace_lab.tracing import TraceSession


class FailingStream:
    """Inject failure around a real file so persisted bytes remain inspectable."""

    def __init__(self, stream, write_at=None, flush_at=None, close_error=None, partial=False):
        self.stream = stream
        self.write_at = write_at
        self.flush_at = flush_at
        self.close_error = close_error
        self.partial = partial
        self.writes = self.flushes = 0
        self.write_error = OSError("injected write failure")

    def write(self, text):
        self.writes += 1
        if self.writes == self.write_at:
            if self.partial:
                self.stream.write(text[:len(text) // 2])
                self.stream.flush()
            raise self.write_error
        return self.stream.write(text)

    def flush(self):
        self.flushes += 1
        if self.flushes == self.flush_at:
            raise OSError("injected flush failure")
        self.stream.flush()

    def close(self):
        self.stream.close()
        if self.close_error is not None:
            raise self.close_error


class ReleaseTracingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "trace.jsonl"

    def stream(self, **kwargs):
        stream = FailingStream(self.path.open("x", encoding="utf-8"), **kwargs)
        self.addCleanup(stream.stream.close)
        return stream

    def test_initialization_preserves_write_error_when_cleanup_also_fails(self):
        stream = self.stream(write_at=1, close_error=RuntimeError("cleanup failed"))
        with patch("agent_trace_lab.tracing.open", return_value=stream, create=True):
            with warnings.catch_warnings(record=True), self.assertRaises(OSError) as caught:
                with TraceSession(self.path):
                    self.fail("initialization must fail before entering the body")
        self.assertIs(caught.exception, stream.write_error)
        self.assertTrue(stream.stream.closed)

    def test_close_error_is_reported_without_replacing_application_error(self):
        stream = self.stream(close_error=OSError("injected close failure"))
        original = LookupError("original application failure")
        session = TraceSession(self.path)
        with patch("agent_trace_lab.tracing.open", return_value=stream, create=True):
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always")
                with self.assertRaises(LookupError) as caught:
                    with session:
                        raise original
        self.assertIs(caught.exception, original)
        self.assertTrue(stream.stream.closed)
        self.assertEqual(session.logging_errors, ("OSError",))
        self.assertTrue(emitted)
        self.assertFalse(audit_trace(load_trace(self.path))["passed"])

    def test_close_error_after_success_is_visible_in_runtime_diagnostics(self):
        stream = self.stream(close_error=OSError("injected close failure"))
        session = TraceSession(self.path)
        result = object()
        with patch("agent_trace_lab.tracing.open", return_value=stream, create=True):
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always")
                with session:
                    @session.tool()
                    def identity():
                        return result
                    self.assertIs(identity(), result)
        self.assertEqual(session.logging_errors, ("OSError",))
        self.assertTrue(emitted)
        self.assertTrue(stream.stream.closed)

    def test_flush_failure_leaves_auditable_incomplete_run(self):
        # run_start and tool_start flush; tool_end flush then fails.
        stream = self.stream(flush_at=3)
        session = TraceSession(self.path)
        with patch("agent_trace_lab.tracing.open", return_value=stream, create=True):
            with warnings.catch_warnings(record=True):
                with session:
                    @session.tool()
                    def tool(value):
                        return value
                    self.assertEqual(tool(37), 37)
                    self.assertEqual(tool(38), 38)  # Recording is now disabled.
        report = audit_trace(load_trace(self.path))
        self.assertFalse(report["passed"])
        self.assertIn("missing_run_end", {issue["code"] for issue in report["issues"]})
        self.assertEqual(session.logging_errors, ("OSError",))
        self.assertEqual(stream.writes, 3)

    def test_partial_write_is_rejected_without_masking_the_tool_exception(self):
        stream = self.stream(write_at=2, partial=True)
        session = TraceSession(self.path)
        original = ValueError("original tool failure")
        with patch("agent_trace_lab.tracing.open", return_value=stream, create=True):
            with warnings.catch_warnings(record=True):
                with self.assertRaises(ValueError) as caught:
                    with session:
                        @session.tool()
                        def fail():
                            raise original
                        fail()
        self.assertIs(caught.exception, original)
        self.assertEqual(session.logging_errors, ("OSError",))
        with self.assertRaisesRegex(ValueError, "line 2: invalid JSON"):
            load_trace(self.path)

    def test_recording_limit_preserves_tools_and_leaves_a_rejected_partial_run(self):
        cap = 1024
        with patch("agent_trace_lab.tracing.MAX_TRACE_BYTES", cap):
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always")
                with TraceSession(self.path) as session:
                    @session.tool()
                    def identity(value):
                        return value
                    results = [identity(number) for number in range(20)]
        self.assertEqual(results, list(range(20)))
        self.assertLessEqual(self.path.stat().st_size, cap)
        self.assertEqual(session.logging_errors, ("TraceLimitError",))
        self.assertEqual(len(emitted), 1)
        events = load_trace(self.path)
        report = audit_trace(events)
        self.assertFalse(report["passed"])
        self.assertIn("missing_run_end", {issue["code"] for issue in report["issues"]})
        self.assertFalse(any(event["type"] == "run_end" for event in events))

    def test_unpaired_surrogates_are_rejected_in_run_and_tool_names(self):
        invalid = "bad\ud800name"
        with self.assertRaisesRegex(ValueError, "valid Unicode"):
            TraceSession(self.path, name=invalid)
        session = TraceSession(self.path)
        with self.assertRaisesRegex(ValueError, "valid Unicode"):
            session.tool(name=invalid)(lambda: None)
        def tool():
            pass
        tool.__name__ = invalid
        with self.assertRaisesRegex(ValueError, "valid Unicode"):
            session.tool()(tool)
        self.assertFalse(self.path.exists())

    def test_surrogates_in_capture_are_replaced_without_changing_tool_values(self):
        payload = {"bad\ud800key": "bad\ud800value"}
        original = ValueError("bad\ud800message")
        with TraceSession(self.path, capture_values=True) as session:
            @session.tool()
            def identity(value):
                return value
            @session.tool()
            def fail():
                raise original
            self.assertIs(identity(payload), payload)
            with self.assertRaises(ValueError) as caught:
                fail()
            self.assertIs(caught.exception, original)
        report = audit_trace(load_trace(self.path))
        self.assertTrue(report["passed"])
        self.assertEqual(session.logging_errors, ())
        recorded = report["calls"][0]["result"]
        self.assertNotEqual(recorded, payload)
        for key, value in recorded.items():
            self.assertNotIn("\ud800", key + value)
            (key + value).encode("utf-8")
        self.assertNotIn("\ud800", report["calls"][1]["error"]["message"])

    def test_thread_still_running_at_exceptional_exit_cannot_append_after_close(self):
        entered, release = threading.Event(), threading.Event()
        session = TraceSession(self.path)
        original = LookupError("application exits early")
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                with self.assertRaises(LookupError) as caught:
                    with session:
                        @session.tool()
                        def waiting():
                            entered.set()
                            if not release.wait(5):
                                raise TimeoutError("test did not release worker")
                            return 42
                        future = pool.submit(waiting)
                        self.assertTrue(entered.wait(5))
                        raise original
                self.assertIs(caught.exception, original)
                before = self.path.read_bytes()
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5), 42)
        self.assertEqual(before, self.path.read_bytes())
        report = audit_trace(load_trace(self.path))
        self.assertFalse(report["passed"])
        self.assertIn("missing_call_end", {issue["code"] for issue in report["issues"]})

    def test_thread_parent_context_requires_explicit_propagation(self):
        with TraceSession(self.path) as session, ThreadPoolExecutor(max_workers=2) as pool:
            @session.tool()
            def child():
                return 1
            @session.tool()
            def parent():
                propagated = pool.submit(contextvars.copy_context().run, child)
                plain = pool.submit(child)
                return propagated.result(timeout=5) + plain.result(timeout=5)
            self.assertEqual(parent(), 2)
        report = audit_trace(load_trace(self.path))
        self.assertTrue(report["passed"])
        parent_id = next(c["call_id"] for c in report["calls"] if c["tool"] == "parent")
        self.assertCountEqual([c["parent_id"] for c in report["calls"] if c["tool"] == "child"],
                              [parent_id, None])

    def test_cancellation_does_not_contaminate_later_async_parent_context(self):
        async def scenario():
            with TraceSession(self.path) as session:
                entered = asyncio.Event()
                @session.tool()
                async def waiting():
                    entered.set()
                    await asyncio.Event().wait()
                @session.tool()
                async def parent():
                    await waiting()
                @session.tool()
                async def afterward():
                    return 42
                task = asyncio.create_task(parent())
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(await afterward(), 42)
        asyncio.run(scenario())
        report = audit_trace(load_trace(self.path))
        self.assertTrue(report["passed"])
        self.assertEqual(report["summary"]["cancelled"], 2)
        self.assertIsNone(next(c["parent_id"] for c in report["calls"] if c["tool"] == "afterward"))

    def test_concurrent_nested_errors_keep_all_calls_paired(self):
        count = 400
        with TraceSession(self.path) as session:
            @session.tool()
            def inner(number):
                if number % 17 == 0:
                    raise ValueError(number)
                return number * 2
            @session.tool()
            def outer(number):
                return inner(number)
            def consume(number):
                try:
                    return outer(number)
                except ValueError as error:
                    return -error.args[0] - 1
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(consume, range(count)))
        self.assertEqual(results, [-n - 1 if n % 17 == 0 else n * 2 for n in range(count)])
        expected_errors = 2 * len(range(0, count, 17))
        report = audit_trace(load_trace(self.path), max_calls=count * 2, max_errors=expected_errors)
        self.assertTrue(report["passed"])
        self.assertEqual(report["summary"]["errors"], expected_errors)
        self.assertEqual(report["summary"]["tool_calls"], count * 2)
        parents = {c["call_id"] for c in report["calls"] if c["tool"] == "outer"}
        self.assertTrue(all(c["parent_id"] in parents for c in report["calls"] if c["tool"] == "inner"))


if __name__ == "__main__":
    unittest.main()
