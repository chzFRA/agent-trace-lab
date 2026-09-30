import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agent_trace_lab.__main__ import main
from agent_trace_lab.report import render_html, write_reports
from agent_trace_lab.runner import run_scenario, run_suite
from agent_trace_lab.scenarios import load_scenario, validate_scenario


def retrieval(**overrides):
    return {"id": "case", "mode": "retrieve", "query": "deployment test suite rollback checkpoint", **overrides}


class RunnerTests(unittest.TestCase):
    def test_retry_uses_new_ids_and_dispatches_only_after_injected_failure(self):
        result, events = run_scenario(retrieval(transient_failures={"read_document": 1}, max_retries=1))
        self.assertEqual([event["status"] for event in events], ["success", "transient_error", "success"])
        self.assertEqual([event["call_id"] for event in events], ["search-1", "read-1", "read-2"])
        self.assertEqual(result["handler_dispatches"], 2)
        self.assertEqual(result["citations"], ["doc:deployment#p1"])
        self.assertTrue(result["task_success"])

    def test_retries_stop_at_retry_limit_and_budget(self):
        result, events = run_scenario(retrieval(transient_failures={"read_document": 8}, max_retries=1))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(len(events), 3)
        result, events = run_scenario(retrieval(transient_failures={"read_document": 8}, max_retries=8, max_calls=2))
        self.assertEqual(result["outcome"], "budget_exhausted")
        self.assertEqual([event["status"] for event in events], ["success", "transient_error", "budget_exhausted"])
        self.assertIsNone(result["answer"])

    def test_missing_evidence_abstains_without_invented_citations(self):
        result, events = run_scenario(retrieval(query="orbital banana telescope", expect={"outcome": "abstained"}))
        self.assertEqual(len(events), 1)
        self.assertEqual(result["citations"], [])
        self.assertIsNone(result["answer"])
        self.assertTrue(result["expected_behavior_passed"])
        self.assertFalse(result["task_success"])

    def test_wrong_citation_expectation_fails_even_when_answer_is_grounded(self):
        result, _ = run_scenario(retrieval(expect={"citations": ["doc:recovery#p1"]}))
        self.assertEqual(result["outcome"], "answered")
        self.assertFalse(result["expected_behavior_passed"])
        self.assertFalse(result["task_success"])

    def test_suite_is_deterministic_and_explicit_about_model_claims(self):
        report, events = run_suite()
        self.assertEqual((report, events), run_suite())
        self.assertEqual(report["summary"]["expected_behavior_passed"], 8)
        self.assertEqual(report["summary"]["retrieval_tasks_answered"], 2)
        self.assertEqual(report["summary"]["retrieval_tasks"], 5)
        self.assertIsNone(report["evaluation"]["model_used"])
        self.assertFalse(report["evaluation"]["real_model_performance_measured"])

    def test_untrusted_content_is_escaped_in_html(self):
        attack = '</pre><script>alert("x")</script><img src=x onerror=alert(1)>'
        report, _ = run_suite([retrieval(id=attack, description=attack)])
        report["scenarios"][0]["answer"] = attack
        report["scenarios"][0]["trace"][0]["tool"] = attack
        page = render_html(report)
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("Content-Security-Policy", page)

    def test_cli_writes_parseable_artifacts_and_fails_for_unmet_expectations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenario = root / "scenario.json"
            scenario.write_text(json.dumps(retrieval(expect={"outcome": "abstained"})), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                status = main(["run", "--scenario", str(scenario), "--output", str(root / "artifacts")])
            self.assertEqual(status, 1)
            report = json.loads((root / "artifacts/report.json").read_text(encoding="utf-8"))
            events = [json.loads(line) for line in (root / "artifacts/traces.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events, report["scenarios"][0]["trace"])
            self.assertTrue((root / "artifacts/report.html").is_file())

    def test_invalid_scenario_rejected_and_cli_reports_input_error(self):
        for scenario in (retrieval(max_calls=True), retrieval(max_retries=-1), retrieval(unknown="x"), retrieval(transient_failures={"shell": 1})):
            with self.subTest(scenario=scenario), self.assertRaises(ValueError):
                validate_scenario(scenario)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["run", "--scenario", "/nonexistent-agent-trace-lab-scenario.json"]), 2)

    def test_nonfinite_numbers_are_rejected_in_nested_scenario_arguments(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            scenario = {"id": "invalid", "mode": "probe", "calls": [
                {"call_id": "one", "tool": "search_docs", "arguments": {"query": [{"nested": value}]}}
            ]}
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_scenario(scenario)

    def test_json_loader_rejects_nonstandard_constants_and_float_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            for token in ("NaN", "Infinity", "-Infinity", "1e400", "-1e400"):
                path.write_text('{"id":"bad","mode":"probe","calls":[{"call_id":"one","tool":"search_docs","arguments":{"query":' + token + '}}]}', encoding="utf-8")
                with self.subTest(token=token), self.assertRaises(ValueError):
                    load_scenario(path)

    def test_serialization_rejects_nonfinite_programmatic_values_before_writing(self):
        for corrupt_report in (True, False):
            report, events = run_suite()
            if corrupt_report:
                report["summary"]["calls_admitted"] = float("inf")
            else:
                events = [{"value": float("nan")}]
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "artifacts"
                with self.subTest(corrupt_report=corrupt_report), self.assertRaises(ValueError):
                    write_reports(report, events, output)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
