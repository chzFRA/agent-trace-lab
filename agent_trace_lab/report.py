"""Self-contained reports with escaped, non-executable scenario content."""

import html
import json
from pathlib import Path
from typing import Any, Dict, List


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def render_html(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    cards = []
    for scenario in report["scenarios"]:
        checks = "".join(
            '<li><span class="{}">{}</span> {}</li>'.format(
                "pass" if check["passed"] else "fail", "PASS" if check["passed"] else "FAIL", _escape(check["name"]))
            for check in scenario["checks"]
        )
        rows = "".join(
            "<tr><td>{}</td><td><code>{}</code></td><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _escape(event["sequence"]), _escape(event["call_id"]), _escape(event["tool"]),
                _escape(event["status"]), "yes" if event["admitted"] else "no", "yes" if event["dispatched"] else "no")
            for event in scenario["trace"]
        )
        answer = "No answer returned."
        if scenario["answer"] is not None:
            answer = _escape(scenario["answer"])
        citations = ", ".join(scenario["citations"]) or "none"
        task_label = "not applicable (executor probe)" if scenario["task_success"] is None else ("answered with expected evidence" if scenario["task_success"] else "not answered successfully")
        cards.append('''<article>
<div class="scenario-heading"><h2>{name}</h2><span class="badge {color}">{badge}</span></div>
<p>{description}</p><p class="meta">Outcome: <strong>{outcome}</strong> · Admitted calls: {calls}/{budget} · Handler dispatches: {dispatches}</p>
<p class="meta">Retrieval task: {task}</p><blockquote>{answer}</blockquote><p class="meta">Citations: <code>{citations}</code></p>
<details><summary>Behavior checks</summary><ul>{checks}</ul></details>
<div class="table-scroll"><table><thead><tr><th>#</th><th>Call ID</th><th>Tool</th><th>Status</th><th>Admitted</th><th>Dispatched</th></tr></thead><tbody>{rows}</tbody></table></div>
<details><summary>Complete trace, including tool outputs</summary><pre>{trace}</pre></details>
</article>'''.format(
            name=_escape(scenario["id"]), color="pass" if scenario["expected_behavior_passed"] else "fail",
            badge="EXPECTED BEHAVIOR" if scenario["expected_behavior_passed"] else "CHECK FAILED",
            description=_escape(scenario["description"]), outcome=_escape(scenario["outcome"]),
            calls=_escape(scenario["calls_admitted"]), budget=_escape(scenario["max_calls"]),
            dispatches=_escape(scenario["handler_dispatches"]), task=_escape(task_label),
            answer=answer, citations=_escape(citations), checks=checks, rows=rows,
            trace=_escape(json.dumps(scenario["trace"], ensure_ascii=False, indent=2, allow_nan=False)),
        ))
    return '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>AgentTrace Lab · Synthetic run report</title>
<style>
:root{{color-scheme:light;--ink:#182938;--muted:#556878;--line:#dce4e9;--green:#096347;--red:#9d2634}}
*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f8;color:var(--ink);font:16px/1.55 system-ui,sans-serif}}
main{{max-width:1120px;margin:auto;padding:42px 24px 72px}}header{{margin-bottom:28px}}h1{{font-size:40px;line-height:1.1;margin:12px 0}}h2{{font-size:21px;margin:0}}
.eyebrow{{font-size:12px;font-weight:750;letter-spacing:.12em;text-transform:uppercase;color:var(--green)}}.intro{{max-width:790px;color:var(--muted)}}
.notice{{border-left:4px solid #c98c21;background:#fff7e6;padding:14px 18px;border-radius:4px}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:24px 0}}
.metric,article{{background:#fff;border:1px solid var(--line);border-radius:12px}}.metric{{padding:20px}}.metric strong{{display:block;font-size:30px}}.metric span,.meta{{color:var(--muted);font-size:14px}}
article{{padding:24px;margin:20px 0}}.scenario-heading{{display:flex;gap:16px;justify-content:space-between;align-items:center;flex-wrap:wrap}}.badge{{font-size:11px;font-weight:750;padding:5px 9px;border-radius:20px;background:#edf6f2}}
.pass{{color:var(--green)}}.fail{{color:var(--red)}}.badge.fail{{background:#faedf0}}blockquote{{margin:18px 0;padding:16px 20px;border-left:3px solid #90a6b6;background:#f6f8fa}}
.table-scroll{{overflow-x:auto;margin:18px 0}}table{{width:100%;border-collapse:collapse;font-size:13px;text-align:left}}th,td{{padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}}th{{color:var(--muted);font-weight:650}}
details{{margin-top:14px}}summary{{cursor:pointer;font-weight:600}}ul{{padding-left:22px}}li{{margin:6px 0}}li span{{font-size:11px;font-weight:750;margin-right:6px}}code,pre{{font-family:ui-monospace,monospace;font-size:12px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7f9;padding:16px;border-radius:8px}}footer{{color:var(--muted);font-size:13px;margin-top:28px}}
@media(max-width:700px){{main{{padding:26px 14px}}h1{{font-size:32px}}.metrics{{grid-template-columns:1fr}}article{{padding:18px}}}}
</style></head><body><main>
<header><div class="eyebrow">AgentTrace Lab / reproducible executor checks</div><h1>Every tool call leaves evidence.</h1>
<p class="intro">A deterministic run through successful retrieval, injected failures, rejected requests, and bounded recovery. Expand a trace to inspect exactly what happened.</p></header>
<aside class="notice"><strong>Scripted policy · synthetic corpus · no model calls.</strong> Behavior-check pass rates describe this executor and these scenarios. They are not LLM performance scores. Timeout failures are injected exceptions; elapsed deadlines, latency, and cost are not measured.</aside>
<section class="metrics" aria-label="Run metrics">
<div class="metric"><strong>{passed}/{total}</strong><span>scenarios met expected behavior</span></div>
<div class="metric"><strong>{answered}/{tasks}</strong><span>retrieval tasks returned expected evidence</span></div>
<div class="metric"><strong>{calls}</strong><span>admitted calls · {dispatches} handler dispatches</span></div>
</section>
<p class="meta">A deliberate abstention or budget stop can pass a behavior check while leaving the retrieval task unanswered. Probe-only scenarios are excluded from retrieval-task counts.</p>
{cards}
<footer>AgentTrace Lab {version} · JSON schema {schema} · All data is local and synthetic. This page has no JavaScript, remote assets, or external requests.</footer>
</main></body></html>'''.format(
        passed=_escape(summary["expected_behavior_passed"]), total=_escape(summary["scenarios"]),
        answered=_escape(summary["retrieval_tasks_answered"]), tasks=_escape(summary["retrieval_tasks"]),
        calls=_escape(summary["calls_admitted"]), dispatches=_escape(summary["handler_dispatches"]),
        cards="\n".join(cards), version=_escape(report["project_version"]), schema=_escape(report["schema_version"]),
    )


def write_reports(report: Dict[str, Any], traces: List[Dict[str, Any]], output: Path) -> None:
    # Validate all serialization before creating any artifacts. Programmatic
    # callers can bypass scenario-file validation, so this is a second guard.
    report_json = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    traces_jsonl = "".join(json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for event in traces)
    report_html = render_html(report)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(report_json, encoding="utf-8")
    (output / "traces.jsonl").write_text(traces_jsonl, encoding="utf-8")
    (output / "report.html").write_text(report_html, encoding="utf-8")
