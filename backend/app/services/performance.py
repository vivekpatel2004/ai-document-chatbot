"""Per-request stage timing without recording prompts or personal data."""

import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar


logger = logging.getLogger("app.performance")
_current_trace: ContextVar["RequestPerformance | None"] = ContextVar(
    "request_performance_trace", default=None
)


class RequestPerformance:
    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.path = path
        self.started = time.perf_counter()
        self.stages_ms: dict[str, float] = {}
        self.database_operations = 0
        self._lock = threading.Lock()

    def add(self, name: str, elapsed_ms: float) -> None:
        with self._lock:
            self.stages_ms[name] = self.stages_ms.get(name, 0.0) + elapsed_ms
            if name == "database":
                self.database_operations += 1


@contextmanager
def measure_stage(name: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        trace = _current_trace.get()
        if trace is not None:
            trace.add(name, (time.perf_counter() - started) * 1000)


def record_stage(name: str, elapsed_ms: float) -> None:
    trace = _current_trace.get()
    if trace is not None:
        trace.add(name, elapsed_ms)


class PerformanceLoggingMiddleware:
    """Log aggregated request timings, including streamed response duration."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        trace = RequestPerformance(
            scope.get("method", "?"), scope.get("path", "?")
        )
        token = _current_trace.set(trace)
        try:
            await self.app(scope, receive, send)
        finally:
            total_ms = (time.perf_counter() - trace.started) * 1000
            logger.info(
                "request_timing method=%s path=%s total_ms=%.1f "
                "database_operations=%d stages_ms=%s",
                trace.method,
                trace.path,
                total_ms,
                trace.database_operations,
                {key: round(value, 1) for key, value in trace.stages_ms.items()},
            )
            _current_trace.reset(token)
