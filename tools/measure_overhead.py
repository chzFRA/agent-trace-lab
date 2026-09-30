"""Measure local recorder overhead; run with `python tools/measure_overhead.py`.

This is an observation script, never a CI timing gate. It compares the same
identity tool with tracing disabled, default tracing, and captured values, both
without delay and with a small simulated wait. Files are temporary and deleted;
optional JSON output retains only aggregate measurements and environment details.
"""

import argparse
import hashlib
import json
import platform
from pathlib import Path
import statistics
import sys
import tempfile
import time

# Running this checked-in script always measures this repository's source.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_trace_lab import __version__
from agent_trace_lab.audit import MAX_TRACE_BYTES, audit_trace, load_trace
from agent_trace_lab.tracing import TraceSession


def measure(path, calls, delay_ms, mode):
    payload = {"query": "local notes", "limit": 3, "api_key": "example-secret"}

    def identity(value):
        if delay_ms:
            time.sleep(delay_ms / 1000)
        return value

    if mode == "untraced":
        started = time.perf_counter_ns()
        for _ in range(calls):
            result = identity(payload)
        elapsed = time.perf_counter_ns() - started
        assert result is payload
        return {"calls": calls, "elapsed_ms": elapsed / 1e6,
                "us_per_call": elapsed / calls / 1000, "trace_bytes": 0, "bytes_per_call": 0}

    # Session initialization/close and decorator construction are outside the
    # timed loop. Per-call timing includes synchronous writes and flushes.
    with TraceSession(path, name="overhead", capture_values=(mode == "capture")) as session:
        traced = session.tool()(identity)
        started = time.perf_counter_ns()
        for _ in range(calls):
            result = traced(payload)
        elapsed = time.perf_counter_ns() - started
        assert result is payload
    if session.logging_errors:
        raise RuntimeError("benchmark trace recording failed: " + repr(session.logging_errors))
    size = path.stat().st_size
    report = audit_trace(load_trace(path), max_calls=calls, max_errors=0)
    if not report["passed"] or report["summary"]["tool_calls"] != calls:
        raise RuntimeError("benchmark did not produce the expected complete trace")
    return {"calls": calls, "elapsed_ms": elapsed / 1e6, "us_per_call": elapsed / calls / 1000,
            "trace_bytes": size, "bytes_per_call": size / calls}


def positive_integer(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=positive_integer, default=1000,
                        help="calls per no-delay sample (default: 1000)")
    parser.add_argument("--delayed-calls", type=positive_integer, default=200,
                        help="calls per delayed sample (default: 200)")
    parser.add_argument("--delay-ms", type=float, default=1.0,
                        help="simulated tool wait in milliseconds (default: 1)")
    parser.add_argument("--repeats", type=positive_integer, default=5)
    parser.add_argument("--output", type=Path, help="optional aggregate JSON result path")
    args = parser.parse_args(argv)
    if not 0 < args.delay_ms < float("inf"):
        parser.error("--delay-ms must be positive and finite")
    results = []
    modes = ("untraced", "default", "capture")
    with tempfile.TemporaryDirectory(prefix="agent-trace-overhead-") as directory:
        for label, calls, delay in (("no_delay", args.calls, 0),
                                    ("simulated_wait", args.delayed_calls, args.delay_ms)):
            samples = {mode: [] for mode in modes}
            for repetition in range(args.repeats):
                # Rotate execution order to reduce a fixed ordering bias.
                order = modes[repetition % len(modes):] + modes[:repetition % len(modes)]
                for mode in order:
                    path = Path(directory) / "{}-{}-{}.jsonl".format(label, repetition, mode)
                    samples[mode].append(measure(path, calls, delay, mode))
            baseline = statistics.median(s["us_per_call"] for s in samples["untraced"])
            for mode in modes:
                times = [s["us_per_call"] for s in samples[mode]]
                median = statistics.median(times)
                results.append({"workload": label, "mode": mode, "calls_per_sample": calls,
                                "simulated_delay_ms": delay, "median_us_per_call": median,
                                "min_us_per_call": min(times), "max_us_per_call": max(times),
                                "extra_us_per_call_vs_untraced": median - baseline,
                                "ratio_vs_untraced": median / baseline if baseline else None,
                                "median_bytes_per_call": statistics.median(s["bytes_per_call"] for s in samples[mode]),
                                "samples": samples[mode]})
    source_root = Path(__file__).resolve().parents[1]
    source_hashes = {name: hashlib.sha256((source_root / "agent_trace_lab" / name).read_bytes()).hexdigest()
                     for name in ("tracing.py", "limits.py") if (source_root / "agent_trace_lab" / name).is_file()}
    output = {"python": platform.python_version(), "platform": platform.platform(),
              "package_version": __version__, "source_sha256": source_hashes, "repeats": args.repeats,
              "loader_limit_bytes": MAX_TRACE_BYTES, "results": results,
              "limitations": ["Local single-thread observation; not a throughput or durability guarantee.",
                              "No LLM/network calls. The delay uses time.sleep, whose scheduling varies.",
                              "Setup/close and report generation are outside the timed call loop.",
                              "Call timing includes Python instrumentation, synchronous file writes and flushes.",
                              "File bytes include two run boundary events; no fsync is performed.",
                              "Medians are compared across independent samples; tiny tools magnify overhead ratios.",
                              "Results depend on hardware, filesystem, load, payload size, and Python version."]}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("workload       mode       median us/call   extra us/call   bytes/call")
    for row in results:
        print("{:<14} {:<10} {:>14.3f} {:>15.3f} {:>12.1f}".format(
            row["workload"], row["mode"], row["median_us_per_call"],
            row["extra_us_per_call_vs_untraced"], row["median_bytes_per_call"]))
    print("Local observation only; timing is not a CI pass/fail threshold.")
    if args.output:
        print("JSON: " + str(args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
