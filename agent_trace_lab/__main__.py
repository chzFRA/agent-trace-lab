"""Command-line entry point. Runs locally without credentials or dependencies."""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .report import write_reports
from .runner import run_suite
from .scenarios import load_scenario
from .audit import audit_trace, compare_traces, load_trace
from .inspector import write_inspection
from .demo import create_demo


def _protect_inputs(paths, output):
    """Reject report outputs that resolve or hard-link to an input trace."""
    for source in paths:
        for filename in ('report.json', 'report.html'):
            target = output/filename
            same = source.resolve() == target.resolve()
            if not same and source.exists() and target.exists():
                same = source.samefile(target)
            if same:
                raise ValueError('Output {} would overwrite an input trace; choose another output directory.'.format(target))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Record, inspect, and compare local Python agent-tool traces.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run built-in scenarios or one custom JSON scenario")
    run_parser.add_argument("--output", type=Path, default=Path("artifacts"), help="Output directory (default: artifacts)")
    run_parser.add_argument("--scenario", type=Path, help="Run one declarative JSON scenario instead of the built-in suite")
    inspect_parser = subparsers.add_parser('inspect', help='Audit a recorded v1 JSONL trace and build an interactive HTML report')
    inspect_parser.add_argument('trace', type=Path)
    inspect_parser.add_argument('--output', type=Path, default=Path('artifacts/inspection'))
    inspect_parser.add_argument('--max-calls', type=int)
    inspect_parser.add_argument('--max-errors', type=int)
    inspect_parser.add_argument('--max-cancelled', type=int)
    inspect_parser.add_argument('--max-duration-ms', type=float)
    compare_parser = subparsers.add_parser('compare', help='Compare two recorded runs and exit nonzero on configured regressions')
    compare_parser.add_argument('before', type=Path)
    compare_parser.add_argument('after', type=Path)
    compare_parser.add_argument('--output', type=Path, default=Path('artifacts/comparison'))
    compare_parser.add_argument('--max-extra-calls', type=int, default=0)
    compare_parser.add_argument('--max-extra-errors', type=int, default=0)
    compare_parser.add_argument('--max-extra-cancelled', type=int, default=0)
    compare_parser.add_argument('--per-tool', action='store_true',
                                help='Also apply count/error/cancellation tolerances to each tool name')
    compare_parser.add_argument('--max-duration-ratio', type=float)
    demo_parser = subparsers.add_parser('demo', help='Record real local file calls: baseline, optimized candidate, and a handled error')
    demo_parser.add_argument('--output', type=Path, default=Path('artifacts/demo'))
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            _protect_inputs([args.trace],args.output)
            report = audit_trace(load_trace(args.trace), max_calls=args.max_calls,
                                 max_errors=args.max_errors,max_cancelled=args.max_cancelled,
                                 max_duration_ms=args.max_duration_ms)
            write_inspection(report,args.output)
            print('{} | {} calls | {} tool errors | {} cancelled | run {}'.format(
                'PASS' if report['passed'] else 'FAIL', report['summary']['tool_calls'],
                report['summary']['errors'],report['summary']['cancelled'],report['summary']['status']))
            for issue in report['issues']:
                print('{}: {}'.format(issue['code'],issue['message']))
            print('Report: {}'.format((args.output/'report.html').resolve()))
            return 0 if report['passed'] else 1
        if args.command == 'compare':
            _protect_inputs([args.before,args.after],args.output)
            before = audit_trace(load_trace(args.before))
            after = audit_trace(load_trace(args.after))
            report = compare_traces(before,after,max_extra_calls=args.max_extra_calls,
                                    max_extra_errors=args.max_extra_errors,
                                    max_extra_cancelled=args.max_extra_cancelled,
                                    per_tool=args.per_tool,max_duration_ratio=args.max_duration_ratio)
            write_inspection(report,args.output,comparison=True)
            print('{} | call delta {:+d} | error delta {:+d}'.format(
                'PASS' if report['passed'] else 'FAIL',report['deltas']['tool_calls'],report['deltas']['errors']))
            for tool in report['per_tool_deltas']:
                changes = tool['deltas']
                if any(changes[key] for key in ('calls', 'errors', 'cancelled')):
                    print('Tool {}: calls {:+d}, errors {:+d}, cancelled {:+d}'.format(
                        tool['name'], changes['calls'], changes['errors'], changes['cancelled']))
            for issue in report['issues']:
                print('{}: {}'.format(issue['code'],issue['message']))
            print('Report: {}'.format((args.output/'report.html').resolve()))
            return 0 if report['passed'] else 1
        if args.command == 'demo':
            results = create_demo(args.output)
            print('Recorded real Python file calls using example notes; no model calls.')
            print('Tool calls: baseline {} -> candidate {}'.format(results['before']['tool_calls'],results['after']['tool_calls']))
            print('Handled file error is included in failure.jsonl; --max-errors 0 rejects it.')
            print('Open {}'.format((args.output/'candidate/report.html').resolve()))
            return 0
        scenarios = [load_scenario(args.scenario)] if args.scenario else None
        report, traces = run_suite(scenarios)
        write_reports(report, traces, args.output)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    summary = report["summary"]
    print("Scripted policy | synthetic scenarios | no model calls")
    print("Expected behavior: {}/{} scenarios; {}/{} checks".format(summary["expected_behavior_passed"], summary["scenarios"], summary["checks_passed"], summary["checks_total"]))
    print("Retrieval tasks answered with expected evidence: {}/{} (not an LLM score)".format(summary["retrieval_tasks_answered"], summary["retrieval_tasks"]))
    print("Reports: {}".format(args.output.resolve()))
    return 0 if summary["expected_behavior_passed"] == summary["scenarios"] else 1


if __name__ == "__main__":
    sys.exit(main())
