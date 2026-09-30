import asyncio
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

from agent_trace_lab.tracing import TraceSession
from agent_trace_lab.audit import MAX_LINE_BYTES, load_trace


class TracingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "trace.jsonl"

    def events(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_sync_nested_identity_signature_and_private_defaults(self):
        result = object()
        session = TraceSession(self.path)

        @session.tool()
        def inner(secret):
            return result

        @session.tool("outer-tool")
        def outer(secret="hidden"):
            return inner(secret)

        self.assertEqual(str(inspect.signature(outer)), "(secret='hidden')")
        self.assertEqual(outer.__name__, "outer")
        with session:
            self.assertIs(outer("do-not-record"), result)
        events = self.events()
        self.assertEqual([e["type"] for e in events],
                         ["run_start", "tool_start", "tool_start", "tool_end", "tool_end", "run_end"])
        self.assertEqual(events[2]["parent_id"], events[1]["call_id"])
        self.assertIsNone(events[1]["parent_id"])
        self.assertEqual([e["seq"] for e in events], list(range(1, 7)))
        self.assertTrue(all(e["run_id"] == session.run_id for e in events))
        self.assertFalse(any("arguments" in e or "result" in e for e in events))
        self.assertNotIn("do-not-record", self.path.read_text())
        self.assertTrue(session._file.closed)
        with self.assertRaises(RuntimeError):
            outer()
        with self.assertRaises(RuntimeError):
            session.__enter__()

    def test_exception_identity_and_default_message_privacy(self):
        error = ValueError("password=supersecret")
        with self.assertRaises(ValueError) as caught:
            with TraceSession(self.path) as session:
                @session.tool()
                def fail():
                    raise error
                fail()
        self.assertIs(caught.exception, error)
        events = self.events()
        self.assertEqual(events[-2]["error"], {"type": "ValueError"})
        self.assertEqual(events[-1]["status"], "error")
        self.assertNotIn("supersecret", self.path.read_text())

    def test_capture_bounds_and_redacts_positional_and_nested_values(self):
        with TraceSession(self.path, capture_values=True) as session:
            @session.tool()
            def example(API_KEY, data):
                return {"Authorization": "hidden-result", "text": "Bearer abc123", "large": "x" * 400}
            example("hidden-argument", {"refreshToken": "nested-hidden", "items": list(range(40))})
        text = self.path.read_text()
        for secret in ("hidden-result", "abc123", "hidden-argument", "nested-hidden"):
            self.assertNotIn(secret, text)
        events = self.events()
        self.assertEqual(events[1]["arguments"]["API_KEY"], "[REDACTED]")
        self.assertEqual(events[1]["arguments"]["data"]["refreshToken"], "[REDACTED]")
        self.assertIn("truncated", events[2]["result"]["large"])
        self.assertEqual(len(events[1]["arguments"]["data"]["items"]), 21)

    def test_async_concurrent_nesting_and_cancellation(self):
        async def scenario():
            with TraceSession(self.path) as session:
                @session.tool()
                async def child(value):
                    await asyncio.sleep(0)
                    return value

                @session.tool()
                async def parent(value):
                    return await child(value)

                @session.tool()
                async def cancelled():
                    raise asyncio.CancelledError()

                self.assertTrue(inspect.iscoroutinefunction(parent))
                self.assertEqual(await asyncio.gather(parent(1), parent(2)), [1, 2])
                with self.assertRaises(asyncio.CancelledError):
                    await cancelled()
        asyncio.run(scenario())
        starts = [e for e in self.events() if e["type"] == "tool_start"]
        parents = {e["call_id"] for e in starts if e["tool"] == "parent"}
        self.assertEqual({e["parent_id"] for e in starts if e["tool"] == "child"}, parents)
        self.assertEqual(self.events()[-2]["status"], "cancelled")
        self.assertIsNone(starts[-1]["parent_id"])

    def test_thread_writes_are_ordered_and_paired(self):
        with TraceSession(self.path) as session:
            @session.tool()
            def square(value):
                return value * value
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(list(pool.map(square, range(60))), [n * n for n in range(60)])
        events = self.events()
        self.assertEqual(len(events), 122)
        self.assertEqual([e["seq"] for e in events], list(range(1, 123)))
        starts = {e["call_id"] for e in events if e["type"] == "tool_start"}
        ends = {e["call_id"] for e in events if e["type"] == "tool_end"}
        self.assertEqual(starts, ends)
        self.assertTrue(all(e["duration_ms"] >= 0 for e in events if "duration_ms" in e))

    def test_existing_file_requires_explicit_overwrite(self):
        self.path.write_text("keep")
        with self.assertRaises(FileExistsError):
            with TraceSession(self.path):
                pass
        self.assertEqual(self.path.read_text(), "keep")
        with TraceSession(self.path, overwrite=True):
            pass
        self.assertEqual(len(self.events()), 2)

    def test_privacy_and_overwrite_flags_require_actual_booleans(self):
        self.path.write_text("keep")
        for option in ("capture_values", "overwrite"):
            for value in ("false", "true", 0, 1, None, [], {}):
                with self.subTest(option=option, value=value):
                    with self.assertRaisesRegex(TypeError, option + " must be a boolean"):
                        with TraceSession(self.path, **{option: value}):
                            self.fail("invalid configuration must never open the file")
                    self.assertEqual(self.path.read_text(), "keep")

    def test_metadata_names_reject_invalid_or_oversized_values(self):
        for name in ("", None, 1, "x" * 257):
            with self.subTest(name=name), self.assertRaises((TypeError, ValueError)):
                TraceSession(self.path, name=name)
        session = TraceSession(self.path)
        for name in ("", 1, "x" * 257):
            with self.subTest(name=name), self.assertRaises((TypeError, ValueError)):
                session.tool(name=name)(lambda: None)
        def tool():
            pass
        tool.__name__ = "x" * 257
        with self.assertRaises(ValueError):
            session.tool()(tool)
        self.assertFalse(self.path.exists())

    def test_maximum_unicode_names_and_large_capture_fit_loader(self):
        name = "🐙" * 256
        leaf = {"secret_{}_{}".format(i, "🐙" * 248): "hidden" for i in range(20)}
        branch = {str(i): leaf for i in range(20)}
        payload = {str(i): branch for i in range(20)}
        with TraceSession(self.path, name=name, capture_values=True) as session:
            @session.tool(name=name)
            def identity(value):
                return value
            self.assertIs(identity(payload), payload)
        events = load_trace(self.path)
        self.assertEqual(events[0]["name"], name)
        self.assertEqual(events[1]["tool"], name)
        self.assertEqual(events[1]["arguments"], "[summary size limit: 64 KiB]")
        self.assertEqual(events[2]["result"], "[summary size limit: 64 KiB]")
        self.assertTrue(all(len(line) <= MAX_LINE_BYTES for line in self.path.read_bytes().splitlines(keepends=True)))

    def test_logging_failure_preserves_results_and_original_errors(self):
        error = LookupError("original")
        with TraceSession(self.path) as session:
            @session.tool()
            def example(fail=False):
                if fail:
                    raise error
                return 42
            with patch.object(session, "_write", side_effect=OSError("disk full")):
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    self.assertEqual(example(), 42)
                    with self.assertRaises(LookupError) as caught:
                        example(True)
                self.assertIs(caught.exception, error)
                self.assertEqual(session.logging_errors, ("OSError",))
        self.assertTrue(session._file.closed)
        self.assertEqual(len(self.events()), 1)

    def test_initial_write_error_closes_file_and_propagates(self):
        session = TraceSession(self.path)
        with patch.object(session, "_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                session.__enter__()
        self.assertTrue(session._file.closed)

    def test_captured_errors_and_cyclic_results_stay_bounded(self):
        cycle = {"items": [float("nan"), 2 ** 500]}
        cycle["self"] = cycle
        with TraceSession(self.path, capture_values=True) as session:
            @session.tool()
            def example(fail=False):
                if fail:
                    raise ValueError("token=hidden Bearer abc123")
                return cycle
            self.assertIs(example(), cycle)
            with self.assertRaises(ValueError):
                example(True)
        text = self.path.read_text()
        self.assertNotIn("hidden", text)
        self.assertNotIn("abc123", text)
        self.assertIn("depth limit", text)
        self.assertEqual(self.events()[-1]["status"], "ok")

    def test_real_task_cancellation_propagates_and_closes_run(self):
        async def scenario():
            with TraceSession(self.path) as session:
                entered = asyncio.Event()
                @session.tool()
                async def sleeping():
                    entered.set()
                    await asyncio.sleep(60)
                task = asyncio.create_task(sleeping())
                await entered.wait()
                task.cancel()
                await task
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(scenario())
        self.assertEqual(self.events()[-2]["status"], "cancelled")
        self.assertEqual(self.events()[-1]["status"], "error")

    def test_generators_rejected_instead_of_misreporting_execution(self):
        def items():
            yield 1
        with self.assertRaises(TypeError):
            TraceSession(self.path).tool()(items)

    def test_unfinished_task_is_reported_and_cannot_write_after_close(self):
        async def scenario():
            session = TraceSession(self.path)
            entered = asyncio.Event()
            finish = asyncio.Event()
            @session.tool()
            async def child():
                entered.set()
                await finish.wait()
            with self.assertRaisesRegex(RuntimeError, "running tools"):
                with session:
                    task = asyncio.create_task(child())
                    await entered.wait()
            snapshot = self.path.read_text()
            finish.set()
            await task
            self.assertEqual(self.path.read_text(), snapshot)
        asyncio.run(scenario())
        self.assertEqual(self.events()[-1]["unfinished_calls"], 1)
        self.assertEqual(self.events()[-1]["status"], "error")


if __name__ == "__main__":
    unittest.main()
