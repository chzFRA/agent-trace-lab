"""Small, dependency-free instrumentation for real Python tool calls."""

import asyncio
import contextvars
from datetime import datetime, timezone
import functools
import inspect
import json
import re
import threading
import time
import uuid
import warnings


_SENSITIVE = re.compile(r"api.?key|token|authorization|password|secret|credential|private.?key", re.I)
_BEARER = re.compile(r"\bBearer\s+[^\s,;\"']+", re.I)
_ASSIGNMENT = re.compile(
    r"\b(api[_ -]?key|token|authorization|password|secret|credential)"
    r"\s*[:=]\s*[^\s,;]+", re.I
)


def _validate_name(value, label):
    if type(value) is not str or not value:
        raise TypeError("{} name must be a nonempty string".format(label))
    if len(value) > 256:
        raise ValueError("{} name must be at most 256 characters".format(label))


def _text(value):
    """Best-effort credential masking; deliberately not a PII classifier."""
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _ASSIGNMENT.sub(lambda m: m.group(1) + "=[REDACTED]", value)
    return value if len(value) <= 256 else value[:256] + "…[truncated]"


def _summary(value, depth=0, budget=None):
    if budget is None:
        budget = [128]
    budget[0] -= 1
    if budget[0] < 0:
        return "[summary limit]"
    kind = type(value)
    if value is None or kind in (bool, int):
        return value if kind is not int or value.bit_length() < 256 else "[large integer]"
    if kind is float:
        return value if float("-inf") < value < float("inf") else "[non-finite float]"
    if kind is str:
        return _text(value)
    if kind in (bytes, bytearray):
        return "[{}: {} bytes]".format(kind.__name__, len(value))
    if kind not in (dict, list, tuple):
        return "[{}]".format(_text(kind.__name__))
    if depth >= 4:
        return "[depth limit]"
    if kind is dict:
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                result["[truncated]"] = len(value) - 20
                break
            label = key if type(key) is str else "[{} key]".format(type(key).__name__)
            result[_text(label)] = "[REDACTED]" if _SENSITIVE.search(label) else _summary(item, depth + 1, budget)
        return result
    result = [_summary(item, depth + 1, budget) for item in value[:20]]
    if len(value) > 20:
        result.append("[{} more items]".format(len(value) - 20))
    return result


def _captured_summary(value):
    summary = _summary(value)
    if len(json.dumps(summary, ensure_ascii=True, allow_nan=False)) > 65536:
        return "[summary size limit: 64 KiB]"
    return summary


class TraceSession:
    """Record one run in JSONL. Join/await all decorated calls before exiting.

    Opening/initializing the file can raise. Once recording starts, logging
    failures warn and disable recording, preserving tool results and errors.
    Inspect ``logging_errors`` to detect incomplete recording programmatically.
    Sessions are single-use. Argument/result capture is off by default.
    """

    def __init__(self, path, name="research", capture_values=False, overwrite=False):
        _validate_name(name, "Run")
        for label, value in (("capture_values", capture_values), ("overwrite", overwrite)):
            if type(value) is not bool:
                raise TypeError("{} must be a boolean".format(label))
        self.path = path
        self.name = name
        self.capture_values = capture_values
        self.overwrite = overwrite
        self.run_id = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._parent = contextvars.ContextVar("trace_parent_" + self.run_id, default=None)
        self._active = False
        self._entered = False
        self._seq = 0
        self._calls = 0
        self._failures = []
        self._file = None

    @property
    def logging_errors(self):
        """Tuple of logging exception type names (no potentially secret messages)."""
        with self._lock:
            return tuple(self._failures)

    def __enter__(self):
        with self._lock:
            if self._entered:
                raise RuntimeError("TraceSession instances are single-use")
            self._file = open(self.path, "w" if self.overwrite else "x", encoding="utf-8")
            self._entered = True
            self._started = time.perf_counter()
            try:
                self._write("run_start", name=self.name, capture_values=self.capture_values)
            except BaseException:
                self._file.close()
                raise
            self._active = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        with self._lock:
            if not self._active:
                return False
            unfinished = self._calls
            fields = {"status": "error" if exc_type or unfinished else "ok",
                      "duration_ms": self._duration(self._started)}
            if unfinished:
                fields["unfinished_calls"] = unfinished
            self._emit("run_end", **fields)
            self._active = False
            try:
                self._file.close()
            except Exception as error:
                self._failure(error)
        if unfinished and exc_type is None:
            raise RuntimeError("TraceSession closed with running tools; join/await tools inside the context")
        return False

    @staticmethod
    def _duration(started):
        return max(0.0, (time.perf_counter() - started) * 1000)

    def _write(self, event_type, **fields):
        self._seq += 1
        event = {"schema_version": 1, "type": event_type, "run_id": self.run_id,
                 "seq": self._seq, "timestamp": datetime.now(timezone.utc).isoformat()}
        event.update(fields)
        self._file.write(json.dumps(event, ensure_ascii=True, allow_nan=False) + "\n")
        self._file.flush()

    def _failure(self, error):
        self._failures.append(type(error).__name__)
        try:
            warnings.warn("Trace recording failed; inspect session.logging_errors. Recording is incomplete.",
                          RuntimeWarning, stacklevel=3)
        except Exception:
            # Warning filters can raise; they must not replace tool outcomes.
            pass

    def _emit(self, event_type, **fields):
        if not self._failures:
            try:
                self._write(event_type, **fields)
            except Exception as error:
                self._failure(error)

    def _begin(self, name, signature, args, kwargs):
        with self._lock:
            if not self._active:
                raise RuntimeError("Decorated tools require an active TraceSession context")
            call_id = uuid.uuid4().hex
            fields = {"call_id": call_id, "parent_id": self._parent.get(), "tool": name}
            if self.capture_values:
                try:
                    values = signature.bind(*args, **kwargs).arguments if signature else {"args": args, "kwargs": kwargs}
                    fields["arguments"] = _captured_summary(dict(values))
                except Exception:
                    fields["arguments"] = "[arguments unavailable]"
            self._calls += 1
            self._emit("tool_start", **fields)
            return call_id, time.perf_counter()

    def _finish(self, name, call_id, started, result=None, error=None):
        with self._lock:
            self._calls -= 1
            if not self._active:
                return
            status = "cancelled" if isinstance(error, asyncio.CancelledError) else "error" if error is not None else "ok"
            fields = {"call_id": call_id, "tool": name, "status": status,
                      "duration_ms": self._duration(started)}
            if error is not None:
                fields["error"] = {"type": _text(type(error).__name__)}
                if self.capture_values:
                    try:
                        fields["error"]["message"] = _text(str(error))
                    except Exception:
                        fields["error"]["message"] = "[message unavailable]"
            elif self.capture_values:
                try:
                    fields["result"] = _captured_summary(result)
                except Exception:
                    fields["result"] = "[result unavailable]"
            self._emit("tool_end", **fields)

    def tool(self, name=None):
        """Decorate an ordinary or async function; preserve its signature/outcome."""
        def decorate(function):
            tool_name = name if name is not None else function.__name__
            _validate_name(tool_name, "Tool")
            if inspect.isgeneratorfunction(function) or inspect.isasyncgenfunction(function):
                raise TypeError("Generator tools are not supported; trace a function that consumes them")
            try:
                signature = inspect.signature(function)
            except (TypeError, ValueError):
                signature = None

            @functools.wraps(function)
            def sync_wrapper(*args, **kwargs):
                call_id, started = self._begin(tool_name, signature, args, kwargs)
                token = self._parent.set(call_id)
                try:
                    result = function(*args, **kwargs)
                except BaseException as error:
                    self._finish(tool_name, call_id, started, error=error)
                    raise
                else:
                    self._finish(tool_name, call_id, started, result=result)
                    return result
                finally:
                    self._parent.reset(token)

            @functools.wraps(function)
            async def async_wrapper(*args, **kwargs):
                call_id, started = self._begin(tool_name, signature, args, kwargs)
                token = self._parent.set(call_id)
                try:
                    result = await function(*args, **kwargs)
                except BaseException as error:
                    self._finish(tool_name, call_id, started, error=error)
                    raise
                else:
                    self._finish(tool_name, call_id, started, result=result)
                    return result
                finally:
                    self._parent.reset(token)

            return async_wrapper if inspect.iscoroutinefunction(function) else sync_wrapper
        return decorate
