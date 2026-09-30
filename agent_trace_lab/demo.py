"""Record actual local file tools, then inspect an optimization and an error."""

from pathlib import Path
from typing import Dict, Any
import tempfile

from .audit import audit_trace, compare_traces, load_trace
from .inspector import write_inspection
from .tracing import TraceSession


def create_demo(output: Path) -> Dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    notes = output/'notes'
    notes.mkdir(exist_ok=True)
    (notes/'tools.md').write_text('Tools expose a narrow, testable capability to an agent.\n',encoding='utf-8')
    (notes/'evidence.md').write_text('Keep evidence provenance separate from answer quality.\n',encoding='utf-8')

    def record(path: Path, redundant: bool) -> None:
        with TraceSession(path,name='notes-baseline' if redundant else 'notes-optimized',overwrite=True) as trace:
            @trace.tool()
            def read_note(name: str) -> str:
                return (notes/name).read_text(encoding='utf-8')

            @trace.tool()
            def count_words(text: str) -> int:
                return len(text.split())

            @trace.tool()
            def summarize_notes() -> int:
                first = read_note('tools.md')
                second = read_note('evidence.md')
                if redundant:
                    # Deliberate unnecessary real file read: the candidate removes it.
                    read_note('tools.md')
                return count_words(first+' '+second)

            summarize_notes()

    record(output/'baseline.jsonl',True)
    record(output/'candidate.jsonl',False)
    before = audit_trace(load_trace(output/'baseline.jsonl'),max_errors=0)
    after = audit_trace(load_trace(output/'candidate.jsonl'),max_errors=0)
    comparison = compare_traces(before,after)
    write_inspection(before,output/'baseline')
    write_inspection(after,output/'candidate')
    write_inspection(comparison,output/'comparison',comparison=True)

    with tempfile.TemporaryDirectory(prefix='missing-note-',dir=output) as empty_directory, TraceSession(output/'failure.jsonl',name='handled-file-error',overwrite=True) as trace:
        @trace.tool()
        def read_missing_note() -> str:
            return (Path(empty_directory)/'not-present.md').read_text(encoding='utf-8')
        try:
            read_missing_note()
        except FileNotFoundError:
            pass  # The application recovers; the tool failure remains in the trace.
    failed = audit_trace(load_trace(output/'failure.jsonl'),max_errors=0)
    write_inspection(failed,output/'failure')
    if not comparison['passed'] or failed['passed'] or failed['summary']['errors'] != 1:
        raise ValueError('Demo output did not match its expected comparison and failure behavior.')
    return {'before':before['summary'],'after':after['summary'],
            'comparison_passed':comparison['passed'],'failure_check_passed':failed['passed']}
