"""Verify request timing logs include aggregated stages."""
import unittest

from app.services.performance import PerformanceLoggingMiddleware, measure_stage
from app.services.database import _TimedConnection
from app.services.performance import RequestPerformance, _current_trace


class PerformanceTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_middleware_logs_measured_request_stages(self):
        async def application(scope, receive, send):
            with measure_stage("authentication"):
                pass
            with measure_stage("database"):
                pass
            await send({
                "type": "http.response.start", "status": 200,
                "headers": [],
            })
            await send({"type": "http.response.body", "body": b"ok"})

        app = PerformanceLoggingMiddleware(application)
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        with self.assertLogs("app.performance", level="INFO") as captured:
            await app({"type": "http", "method": "GET", "path": "/test"}, receive, send)
        self.assertEqual(len(messages), 2)
        self.assertIn("authentication", captured.output[0])
        self.assertIn("database", captured.output[0])
        self.assertIn("total_ms=", captured.output[0])

    async def test_database_statement_proxy_records_safe_operation_labels(self):
        class FakeConnection:
            def execute(self, query, parameters=()):
                return query, parameters

        trace = RequestPerformance("POST", "/api/chat/")
        token = _current_trace.set(trace)
        try:
            result = _TimedConnection(FakeConnection()).execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                ("private content", "message-id"),
            )
        finally:
            _current_trace.reset(token)

        self.assertEqual(result[0].split()[0], "UPDATE")
        self.assertEqual(trace.database_statement_counts, {"update_messages": 1})
        self.assertGreaterEqual(trace.stages_ms["db_statement:update_messages"], 0.0)


if __name__ == "__main__":
    unittest.main()
