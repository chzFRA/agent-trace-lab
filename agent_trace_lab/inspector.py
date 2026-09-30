"""Portable HTML reports for recorded tool calls and regression comparisons."""

import html
import json
from pathlib import Path
from typing import Any, Dict


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


_STYLE = """
:root{color-scheme:light;--ink:#142d3d;--muted:#597080;--line:#dce5e9;--accent:#006c67}
*{box-sizing:border-box}body{margin:0;background:#f5f8f9;color:var(--ink);font:15px/1.6 system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:44px 24px 80px}header{border-bottom:1px solid var(--line);padding-bottom:24px}
.eyebrow{color:var(--accent);font-size:12px;letter-spacing:.14em;font-weight:700;text-transform:uppercase}
h1{font-size:clamp(28px,5vw,42px);letter-spacing:-.04em;line-height:1.15;margin:14px 0}h2{font-size:20px;margin:32px 0 12px}
p{margin:8px 0}.muted{color:var(--muted)}.pill{display:inline-block;border:1px solid var(--line);border-radius:20px;padding:3px 11px;background:white;font-size:12px}
.ok{color:#00634b}.error,.cancelled,.incomplete{color:#a63333}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:24px 0}
.card{background:white;border:1px solid var(--line);border-radius:12px;padding:20px}.card b{display:block;font-size:29px;line-height:1.2}.card span{color:var(--muted);font-size:13px}
.toolbar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:16px}input,select{font:inherit;border:1px solid #b8cbd3;border-radius:8px;padding:9px;background:white}input{min-width:240px;flex:1}
details{background:white;border:1px solid var(--line);border-radius:9px;margin:9px 0;overflow:hidden}summary{cursor:pointer;padding:15px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}summary strong{flex:1;overflow-wrap:anywhere}
.call-body{border-top:1px solid var(--line);padding:16px}.call-id{font:12px ui-monospace,monospace;color:var(--muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace;background:#f2f6f7;padding:15px;border-radius:6px;max-height:420px;overflow:auto}
.notice{border-left:3px solid #a6bec5;background:#edf3f5;padding:12px 16px;margin:14px 0}.issue{border-left-color:#be6666}
table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--line)}.scroll{overflow-x:auto}footer{margin-top:36px;border-top:1px solid var(--line);padding-top:16px;font-size:12px;color:var(--muted)}
@media print{.toolbar{display:none}main{padding:10px}details{break-inside:avoid}}
"""

_SCRIPT = """
const query=document.getElementById('search');
const status=document.getElementById('status');
function filterCalls(){
 const text=query.value.toLocaleLowerCase();let visible=0;
 document.querySelectorAll('[data-call]').forEach(row=>{
  const show=row.dataset.tool.toLocaleLowerCase().includes(text)&&(!status.value||row.dataset.status===status.value);
  row.hidden=!show;if(show)visible++;
 });
 document.getElementById('visible-count').textContent=visible+' calls shown';
}
if(query){query.addEventListener('input',filterCalls);status.addEventListener('change',filterCalls);filterCalls();}
"""


def _page(title: str, body: str) -> str:
    # Dynamic content never enters script, CSS, or event-handler contexts.
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>""" + _escape(title) + " — AgentTrace Lab</title><style>" + _STYLE + "</style></head><body><main>" + body + """<footer>
AgentTrace Lab · Local trace inspection · No analytics, external fonts, or network requests.
Durations include nested calls and are observations, not model-quality scores.
</footer></main><script>""" + _SCRIPT + "</script></body></html>"


def _issues(issues: list) -> str:
    if not issues:
        return '<p class="notice">No structural problems or configured budget violations found.</p>'
    return ''.join('<div class="notice issue"><strong>' + _escape(item['code']) + '</strong> · ' + _escape(item['severity']) + '<p>' + _escape(item['message']) + '</p></div>' for item in issues)


def render_inspection(report: Dict[str, Any]) -> str:
    summary = report['summary']
    header = '<header><div class="eyebrow">AgentTrace Lab / execution inspector</div><h1>' + _escape(summary['name']) + '</h1><p class="muted">Inspect real Python tool calls, their errors, and their parent relationships.</p><span class="pill">' + ('Checks passed' if report['passed'] else 'Checks failed') + '</span> <span class="pill">Run status: ' + _escape(summary['status']) + '</span><p class="call-id">' + _escape(summary['run_id']) + '</p></header>'
    metrics = [(summary['tool_calls'], 'Tool calls'), (summary['errors'], 'Tool errors'), (summary['cancelled'], 'Cancelled calls'), ('{:.2f} ms'.format(summary['duration_ms']), 'Run duration')]
    cards = '<div class="cards">' + ''.join('<div class="card"><b>' + _escape(value) + '</b><span>' + label + '</span></div>' for value,label in metrics) + '</div>'
    calls = []
    for call in report['calls']:
        status = call['status']
        color = status if status in ('ok','error','cancelled','incomplete') else 'incomplete'
        details = {key:value for key,value in call.items() if key not in ('tool','status','duration_ms','depth')}
        calls.append('<details data-call data-tool="' + _escape(call['tool']) + '" data-status="' + _escape(status) + '"><summary><span class="call-id">#' + _escape(call['start_seq']) + '</span><strong>' + _escape(call['tool']) + '</strong><span class="pill ' + color + '">' + _escape(status) + '</span><span>' + '{:.2f} ms'.format(call['duration_ms']) + '</span><span class="muted">depth ' + _escape(call['depth']) + '</span></summary><div class="call-body"><pre>' + _escape(_json(details)) + '</pre></div></details>')
    tools = ''.join('<tr><td>' + _escape(tool['name']) + '</td><td>' + str(tool['calls']) + '</td><td>' + str(tool['errors']) + '</td><td>' + '{:.2f}'.format(tool['duration_ms']) + '</td></tr>' for tool in summary['tools'])
    body = header + cards + '<h2>Checks</h2>' + _issues(report['issues']) + '<h2>Tool breakdown</h2><div class="scroll"><table><thead><tr><th>Tool</th><th>Calls</th><th>Errors</th><th>Inclusive duration (ms)</th></tr></thead><tbody>' + tools + '</tbody></table></div><h2>Call details</h2><div class="toolbar"><label for="search">Filter tools</label><input id="search" placeholder="Search a tool name" type="search"><label for="status">Status</label><select id="status"><option value="">All statuses</option><option>ok</option><option>error</option><option>cancelled</option><option>incomplete</option></select><span id="visible-count" class="muted"></span></div>' + ''.join(calls)
    body += '<p class="notice">Arguments, results, and error messages are omitted by default. If capture was explicitly enabled, captured values appear in each call. A successful call does not prove its answer is correct.</p>'
    return _page(str(summary['name']), body)


def render_comparison(comparison: Dict[str, Any]) -> str:
    before, after = comparison['before'], comparison['after']
    rows = []
    for key,label in (('tool_calls','Tool calls'),('errors','Tool errors'),('duration_ms','Duration (ms)')):
        left,right = before[key],after[key]
        delta = right-left
        rows.append('<tr><th>' + label + '</th><td>' + _escape(round(left,3)) + '</td><td>' + _escape(round(right,3)) + '</td><td>' + _escape('{:+.3f}'.format(delta) if key=='duration_ms' else '{:+d}'.format(delta)) + '</td></tr>')
    body = '<header><div class="eyebrow">AgentTrace Lab / run comparison</div><h1>' + ('Regression checks passed' if comparison['passed'] else 'Regression detected') + '</h1><p class="muted">' + _escape(before['name']) + ' → ' + _escape(after['name']) + '</p></header><h2>Observed changes</h2><div class="scroll"><table><thead><tr><th>Metric</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div><h2>Configured checks</h2>' + _issues(comparison['issues']) + '<p class="notice">Compare runs with the same task and inputs. Timing varies with environment and workload; duration checking is opt-in. These checks do not measure answer quality, tokens, or model cost.</p>'
    return _page('Run comparison',body)


def write_inspection(report: Dict[str, Any], output: Path, comparison: bool = False) -> None:
    data = _json(report)
    page = render_comparison(report) if comparison else render_inspection(report)
    output.mkdir(parents=True, exist_ok=True)
    (output/'report.json').write_text(data+'\n',encoding='utf-8')
    (output/'report.html').write_text(page,encoding='utf-8')
