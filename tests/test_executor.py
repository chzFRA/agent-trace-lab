import unittest

from agent_trace_lab.engine import Tool, ToolExecutor, read_document, search_docs


class ExecutorContractTests(unittest.TestCase):
    def setUp(self):
        self.seen = []
        self.tool = Tool("record", {"value": str}, lambda value: self.seen.append(value) or {"value": value})

    def executor(self, **kwargs):
        return ToolExecutor(tools={"record": self.tool}, **kwargs)

    def test_duplicate_id_with_changed_arguments_cannot_repeat_side_effect(self):
        executor = self.executor(max_calls=1)
        first = executor.execute("stable-id", "record", {"value": "first"})
        duplicate = executor.execute("stable-id", "record", {"value": "changed"})
        self.assertEqual(first["status"], "success")
        self.assertEqual(duplicate["status"], "duplicate_call_id")
        self.assertFalse(duplicate["admitted"])
        self.assertFalse(duplicate["dispatched"])
        self.assertEqual(self.seen, ["first"])
        self.assertEqual((executor.call_count, executor.dispatch_count), (1, 1))

    def test_invalid_and_unknown_requests_consume_budget_without_dispatch(self):
        executor = self.executor(max_calls=2)
        self.assertEqual(executor.execute("a", "record", {"value": 8})["status"], "invalid_arguments")
        self.assertEqual(executor.execute("b", "missing", {})["status"], "unknown_tool")
        third = executor.execute("c", "record", {"value": "never"})
        self.assertEqual(third["status"], "budget_exhausted")
        self.assertEqual(self.seen, [])
        self.assertEqual(executor.call_count, 2)
        self.assertEqual([event["calls_used"] for event in executor.events], [1, 2, 2])

    def test_schema_rejects_missing_extra_blank_and_non_object_arguments(self):
        executor = self.executor(max_calls=5)
        for index, arguments in enumerate(({}, {"value": "x", "extra": 1}, {"value": " "}, [])):
            with self.subTest(arguments=arguments):
                self.assertEqual(executor.execute(str(index), "record", arguments)["status"], "invalid_arguments")
        self.assertEqual(self.seen, [])

    def test_validation_precedes_fault_injection_and_returned_events_are_copies(self):
        executor = self.executor(transient_failures={"record": 1})
        executor.execute("invalid", "record", {"value": False})
        failed = executor.execute("retry-1", "record", {"value": "ok"})
        self.assertEqual(failed["status"], "transient_error")
        self.assertTrue(failed["injected_failure"])
        self.assertFalse(failed["dispatched"])
        succeeded = executor.execute("retry-2", "record", {"value": "ok"})
        succeeded["result"]["value"] = "tampered"
        succeeded["arguments"]["value"] = "tampered"
        self.assertEqual(executor.events[-1]["result"]["value"], "ok")
        self.assertEqual(executor.events[-1]["arguments"]["value"], "ok")
        self.assertEqual(self.seen, ["ok"])

    def test_invalid_limits_rejected(self):
        for budget in (0, -1, True, 101):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                self.executor(max_calls=budget)
        with self.assertRaises(ValueError):
            self.executor(transient_failures={"unregistered": 1})

    def test_handler_failure_is_recorded_without_automatic_retry(self):
        def broken(value):
            raise RuntimeError("example failure")
        executor = ToolExecutor(tools={"broken": Tool("broken", {"value": str}, broken)})
        event = executor.execute("once", "broken", {"value": "x"})
        self.assertEqual(event["status"], "tool_error")
        self.assertTrue(event["dispatched"])
        self.assertEqual(len(executor.events), 1)

    def test_search_and_read_produce_stable_citable_evidence(self):
        self.assertEqual(search_docs("credentials environment secrets")["matches"][0]["document_id"], "credentials")
        self.assertEqual(search_docs("orbital banana telescope")["matches"], [])
        evidence = read_document("credentials")["evidence"]
        self.assertEqual(evidence[0]["citation_id"], "doc:credentials#p1")
        evidence[0]["text"] = "modified"
        self.assertNotEqual(read_document("credentials")["evidence"][0]["text"], "modified")
        event = ToolExecutor().execute("missing", "read_document", {"document_id": "absent"})
        self.assertEqual(event["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
