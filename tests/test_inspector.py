import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from agent_trace_lab import TraceSession
from agent_trace_lab.__main__ import main
from agent_trace_lab.audit import audit_trace, load_trace
from agent_trace_lab.demo import create_demo
from agent_trace_lab.inspector import render_inspection, write_inspection


class InspectorIntegrationTests(unittest.TestCase):
    def test_input_trace_cannot_be_overwritten_by_reports_or_hardlinks(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            root=Path(directory)
            create_demo(root/'demo')
            data=(root/'demo/candidate.jsonl').read_bytes()
            source=root/'report.json'
            source.write_bytes(data)
            self.assertEqual(main(['inspect',str(source),'--output',str(root)]),2)
            self.assertEqual(source.read_bytes(),data)
            candidate=root/'report.html'
            candidate.write_bytes(data)
            self.assertEqual(main(['compare',str(root/'demo/baseline.jsonl'),str(candidate),'--output',str(root)]),2)
            self.assertEqual(candidate.read_bytes(),data)
            linked=root/'linked'
            linked.mkdir()
            os.link(source,linked/'report.json')
            self.assertEqual(main(['inspect',str(source),'--output',str(linked)]),2)
            self.assertEqual(source.read_bytes(),data)

    def test_demo_failure_does_not_depend_on_existing_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'notes').mkdir()
            existing=root/'notes/not-present.md'
            existing.write_text('Preserve this existing file.')
            result=create_demo(root)
            self.assertFalse(result['failure_check_passed'])
            self.assertEqual(existing.read_text(),'Preserve this existing file.')
            self.assertEqual(audit_trace(load_trace(root/'failure.jsonl'))['summary']['errors'],1)

    def test_real_recording_cli_budgets_and_reverse_regression(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory)
            result = create_demo(output)
            self.assertEqual(result['before']['tool_calls'],5)
            self.assertEqual(result['after']['tool_calls'],4)
            self.assertTrue(result['comparison_passed'])
            self.assertFalse(result['failure_check_passed'])
            report = json.loads((output/'candidate/report.json').read_text())
            self.assertEqual(report['summary']['max_depth'],2)
            self.assertEqual(main(['inspect',str(output/'candidate.jsonl'),'--max-calls','4','--max-errors','0','--output',str(output/'check')]),0)
            self.assertEqual(main(['inspect',str(output/'failure.jsonl'),'--max-errors','0','--output',str(output/'failed-check')]),1)
            self.assertEqual(main(['compare',str(output/'candidate.jsonl'),str(output/'baseline.jsonl'),'--output',str(output/'regression')]),1)
            self.assertEqual(main(['compare',str(output/'candidate.jsonl'),str(output/'baseline.jsonl'),'--max-extra-calls','1','--output',str(output/'allowed')]),0)

    def test_recorded_untrusted_values_do_not_become_html_or_script(self):
        payload = '</script><img src=x onerror=alert(1)><script>alert(2)</script>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'trace.jsonl'
            with TraceSession(path,name=payload,capture_values=True) as trace:
                @trace.tool(name=payload)
                def identity(text):
                    return text
                identity(payload)
            report = audit_trace(load_trace(path))
            page = render_inspection(report)
            self.assertNotIn(payload,page)
            self.assertNotIn('<img',page)
            self.assertNotIn('<script>alert',page)
            self.assertIn('&lt;script&gt;',page)
            self.assertEqual(page.count('<script>'),1)
            self.assertIn('id="search"',page)
            self.assertIn('id="status"',page)
            write_inspection(report,Path(directory)/'report')
            self.assertEqual(json.loads((Path(directory)/'report/report.json').read_text()),report)

    def test_cli_rejects_bad_input_and_invalid_thresholds(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            path=Path(directory)/'invalid.jsonl'
            path.write_text('{broken}\n')
            self.assertEqual(main(['inspect',str(path),'--output',str(Path(directory)/'out')]),2)
            create_demo(Path(directory)/'demo')
            self.assertEqual(main(['inspect',str(Path(directory)/'demo/candidate.jsonl'),'--max-calls','-1']),2)
            self.assertEqual(main(['compare',str(Path(directory)/'demo/baseline.jsonl'),str(Path(directory)/'demo/candidate.jsonl'),'--max-duration-ratio','nan']),2)


if __name__ == '__main__':
    unittest.main()
