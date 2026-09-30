"""Scripted retrieval policy and checks for synthetic executor scenarios."""

from typing import Any, Dict, List, Tuple

from . import __version__
from .engine import ToolExecutor
from .scenarios import builtin_scenarios, validate_scenario


def _request_with_retry(executor: ToolExecutor, prefix: str, tool: str,
                        arguments: Dict[str, Any], max_retries: int) -> Dict[str, Any]:
    for attempt in range(max_retries + 1):
        event = executor.execute("{}-{}".format(prefix, attempt + 1), tool, arguments)
        if event["status"] != "transient_error":
            return event
    return event


def _retrieve(scenario: Dict[str, Any], executor: ToolExecutor) -> Dict[str, Any]:
    result = {"outcome": "failed", "answer": None, "citations": []}
    search = _request_with_retry(executor, "search", "search_docs", {"query": scenario["query"]}, scenario["max_retries"])
    if search["status"] != "success":
        if search["status"] == "budget_exhausted":
            result["outcome"] = "budget_exhausted"
        return result
    matches = search["result"]["matches"]
    if not matches:
        result["outcome"] = "abstained"
        return result
    document = _request_with_retry(executor, "read", "read_document", {"document_id": matches[0]["document_id"]}, scenario["max_retries"])
    if document["status"] != "success":
        if document["status"] == "budget_exhausted":
            result["outcome"] = "budget_exhausted"
        return result
    # This deterministic policy extracts the first paragraph; it does not
    # generate or semantically validate an answer with a language model.
    evidence = document["result"]["evidence"][0]
    return {"outcome": "answered", "answer": evidence["text"], "citations": [evidence["citation_id"]]}


def run_scenario(raw_scenario: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    scenario = validate_scenario(raw_scenario)
    executor = ToolExecutor(max_calls=scenario["max_calls"], transient_failures=scenario["transient_failures"], scenario_id=scenario["id"])
    if scenario["mode"] == "retrieve":
        result = _retrieve(scenario, executor)
    else:
        for call in scenario["calls"]:
            executor.execute(call["call_id"], call["tool"], call["arguments"])
        last_status = executor.events[-1]["status"]
        outcome = "completed" if last_status == "success" else "rejected"
        if last_status == "budget_exhausted":
            outcome = "budget_exhausted"
        if last_status in ("transient_error", "tool_error", "not_found"):
            outcome = "failed"
        result = {"outcome": outcome, "answer": None, "citations": []}

    events = executor.events
    statuses = [event["status"] for event in events]
    successful_evidence = {
        evidence["citation_id"]: evidence["text"]
        for event in events
        if event["tool"] == "read_document" and event["status"] == "success"
        for evidence in event["result"]["evidence"]
    }
    citations_grounded = all(citation in successful_evidence for citation in result["citations"])
    answer_grounded = result["answer"] is None or (
        bool(result["citations"]) and citations_grounded and
        result["answer"] in [successful_evidence[citation] for citation in result["citations"]]
    )
    dispatched_ids = [event["call_id"] for event in events if event["dispatched"]]
    invalid_statuses = {"invalid_arguments", "unknown_tool", "duplicate_call_id", "budget_exhausted"}
    checks = [
        {"name": "admitted calls stay within budget", "passed": executor.call_count <= executor.max_calls},
        {"name": "rejected requests never dispatch a handler", "passed": all(not event["dispatched"] for event in events if event["status"] in invalid_statuses)},
        {"name": "each admitted call ID dispatches at most once", "passed": len(dispatched_ids) == len(set(dispatched_ids))},
        {"name": "answer and citations come from returned evidence", "passed": citations_grounded and answer_grounded},
    ]
    expected = scenario["expect"]
    if "outcome" in expected:
        checks.append({"name": "expected outcome: {}".format(expected["outcome"]), "passed": result["outcome"] == expected["outcome"]})
    if "statuses" in expected:
        checks.append({"name": "expected statuses observed", "passed": set(expected["statuses"]).issubset(statuses)})
    if "citations" in expected:
        checks.append({"name": "expected citations returned", "passed": sorted(result["citations"]) == sorted(expected["citations"])})
    task_success = None
    if scenario["mode"] == "retrieve":
        task_success = result["outcome"] == "answered" and citations_grounded and answer_grounded
        if "citations" in expected:
            task_success = task_success and sorted(result["citations"]) == sorted(expected["citations"])

    return {
        "id": scenario["id"],
        "description": scenario["description"],
        "mode": scenario["mode"],
        "policy": "scripted",
        "query": scenario.get("query"),
        **result,
        "task_success": task_success,
        "expected_behavior_passed": all(check["passed"] for check in checks),
        "checks": checks,
        "calls_admitted": executor.call_count,
        "handler_dispatches": executor.dispatch_count,
        "requests_recorded": len(events),
        "max_calls": executor.max_calls,
        "max_retries_per_tool": scenario["max_retries"],
        "injected_failures": sum(event["injected_failure"] for event in events),
        "trace": events,
    }, events


def run_suite(scenarios: List[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    selected = builtin_scenarios() if scenarios is None else scenarios
    if not selected:
        raise ValueError("At least one scenario is required.")
    ids = [scenario.get("id") for scenario in selected]
    if len(ids) != len(set(ids)):
        raise ValueError("Scenario IDs must be unique within a suite.")
    results = []
    traces = []
    for scenario in selected:
        result, events = run_scenario(scenario)
        results.append(result)
        traces.extend(events)
    retrieval_results = [result for result in results if result["mode"] == "retrieve"]
    summary = {
        "scenarios": len(results),
        "expected_behavior_passed": sum(result["expected_behavior_passed"] for result in results),
        "checks_passed": sum(check["passed"] for result in results for check in result["checks"]),
        "checks_total": sum(len(result["checks"]) for result in results),
        "retrieval_tasks": len(retrieval_results),
        "retrieval_tasks_answered": sum(result["task_success"] for result in retrieval_results),
        "abstentions": sum(result["outcome"] == "abstained" for result in retrieval_results),
        "calls_admitted": sum(result["calls_admitted"] for result in results),
        "handler_dispatches": sum(result["handler_dispatches"] for result in results),
    }
    return {
        "schema_version": 1,
        "project_version": __version__,
        "evaluation": {
            "policy": "scripted",
            "dataset": "synthetic",
            "model_used": None,
            "real_model_performance_measured": False,
            "wall_clock_timeouts_measured": False,
            "latency_and_cost_measured": False,
            "scope": "Executor-contract checks and a deterministic lexical retrieval baseline; no LLM evaluation.",
            "task_success_definition": "Retrieve mode returned an exact source paragraph with grounded citations, matching declared citation expectations when present. This is not semantic answer-quality evaluation.",
        },
        "summary": summary,
        "scenarios": results,
    }, traces
