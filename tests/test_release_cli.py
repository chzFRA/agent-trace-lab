"""Acceptance checks for actionable CLI and report diagnostics."""

import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from agent_trace_lab import TraceSession
from agent_trace_lab.__main__ import main
from agent_trace_lab.audit import audit_trace, compare_traces, load_trace
from agent_trace_lab.inspector import render_comparison, render_inspection


class ReleaseInterfaceTests(unittest.TestCase):
    def test_cli_identifies_tool_error_hidden_by_equal_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, fails in (("before.jsonl", "search"), ("after.jsonl", "read")):
                with TraceSession(root / filename) as trace:
                    for name in ("search", "read"):
                        @trace.tool(name)
                        def tool():
                            if name == fails:
                                raise ValueError("deliberate failure")
                        try:
                            tool()
                        except ValueError:
                            pass
            output = io.StringIO()
            command = ['compare', str(root/'before.jsonl'), str(root/'after.jsonl'),
                       '--output', str(root/'comparison')]
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(command), 0)
                self.assertEqual(main(command + ['--per-tool']), 1)
            report = json.loads((root/'comparison/report.json').read_text())
            self.assertEqual(report['deltas']['errors'], 0)
            self.assertIn('Tool read: calls +0, errors +1, cancelled +0', output.getvalue())
            page = (root/'comparison/report.html').read_text()
            self.assertIn('Which tools changed?', page)
            self.assertIn('<th>read</th>', page)
            self.assertIn('both in total and per tool', page)

    def test_cancelled_tool_can_fail_explicit_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            async def scenario():
                with TraceSession(root/'cancelled.jsonl') as trace:
                    @trace.tool()
                    async def cancelled():
                        raise asyncio.CancelledError()
                    try:
                        await cancelled()
                    except asyncio.CancelledError:
                        pass
            asyncio.run(scenario())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['inspect', str(root/'cancelled.jsonl'),
                                       '--max-cancelled', '0', '--output', str(root/'out')]), 1)
            self.assertIn('1 cancelled', output.getvalue())

    def test_parent_names_are_visible_and_untrusted_names_are_escaped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'trace.jsonl'
            label = '<img src=x onerror=alert(1)>'
            with TraceSession(path) as trace:
                @trace.tool(label)
                def parent():
                    child()
                @trace.tool()
                def child():
                    return 1
                parent()
            report = audit_trace(load_trace(path))
            page = render_inspection(report)
            self.assertIn('Called by: &lt;img', page)
            comparison = render_comparison(compare_traces(report, report, per_tool=True))
            self.assertNotIn('<img', comparison)
            self.assertIn('&lt;img', comparison)
            self.assertIn('Cancelled calls', comparison)


if __name__ == '__main__':
    unittest.main()
