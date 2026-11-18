"""Coverage for parameterized Turso transaction pipelines."""

import unittest
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.db_connection import execute_transaction_batch
from app.services import database


class FakeSession:
    def __init__(self, failure_step=None):
        self.failure_step = failure_step
        self.requests = []

    def execute_pipeline(self, requests):
        self.requests.append(requests)
        batch = requests[0]["batch"]
        count = len(batch["steps"])
        errors = [None] * count
        results = []
        for step in batch["steps"]:
            sql = step["stmt"]["sql"].strip().upper()
            result = {"affected_row_count": 1}
            if sql.startswith("SELECT"):
                result["rows"] = [[{"type": "text", "value": "doc-a"}]]
            results.append(result)
        if self.failure_step is not None:
            errors[self.failure_step] = {"message": "simulated SQL failure"}
            for index in range(self.failure_step + 1, count - 1):
                results[index] = None
        return [{
            "type": "ok",
            "response": {
                "type": "batch",
                "result": {"step_results": results, "step_errors": errors},
            },
        }]


class TursoPipelineTests(unittest.TestCase):
    def test_pipeline_binds_values_and_commits_in_one_request(self):
        session = FakeSession()
        connection = SimpleNamespace(_session=session)

        counts = execute_transaction_batch(connection, [
            ("INSERT INTO messages(id, content) VALUES (?, ?)", ("m1", "hello ?")),
            ("UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", ("c1",)),
        ])

        self.assertEqual(counts, [1, 1])
        self.assertEqual(len(session.requests), 1)
        steps = session.requests[0][0]["batch"]["steps"]
        self.assertEqual(steps[1]["stmt"]["sql"], "INSERT INTO messages(id, content) VALUES (?, ?)")
        self.assertEqual(
            [value["value"] for value in steps[1]["stmt"]["args"]],
            ["m1", "hello ?"],
        )
        self.assertEqual(steps[1]["condition"], {"type": "ok", "step": 0})
        self.assertEqual(steps[3]["stmt"]["sql"], "COMMIT")
        self.assertEqual(steps[4]["stmt"]["sql"], "ROLLBACK")

    def test_pipeline_decodes_parameterized_select_rows(self):
        session = FakeSession()
        connection = SimpleNamespace(_session=session)

        result = execute_transaction_batch(connection, [
            ("SELECT id FROM documents WHERE owner_id = ?", ("owner-a",), True),
        ])

        self.assertEqual(result[0]["rows"], [("doc-a",)])
        self.assertEqual(len(session.requests), 1)

    def test_failed_statement_raises_and_batch_includes_conditional_rollback(self):
        session = FakeSession(failure_step=1)
        connection = SimpleNamespace(_session=session)

        with self.assertRaisesRegex(RuntimeError, "simulated SQL failure"):
            execute_transaction_batch(connection, [("UPDATE documents SET status = ?", ("processed",))])

        steps = session.requests[0][0]["batch"]["steps"]
        self.assertEqual(steps[-1]["stmt"]["sql"], "ROLLBACK")
        self.assertEqual(steps[-1]["condition"], {
            "type": "not", "cond": {"type": "ok", "step": 2}
        })

    def test_existing_chat_context_checks_conversation_and_document_owner_together(self):
        path = Path(tempfile.gettempdir()) / f"chat-context-{os.getpid()}.sqlite3"
        path.unlink(missing_ok=True)
        try:
            with patch.dict(os.environ, {"DATABASE_PROVIDER": "sqlite", "DATABASE_URL": str(path)}):
                database.init_db()
                database.execute(
                    "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
                    ("owner-a", "A", "a@example.test", "hash"),
                )
                database.execute(
                    "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
                    ("owner-b", "B", "b@example.test", "hash"),
                )
                database.create_conversation("chat-a", owner_id="owner-a")
                database.create_document(
                    "doc-a", None, "a.pdf", "pdf", 1, "a-key",
                    owner_id="owner-a",
                )
                database.create_document(
                    "doc-b", None, "b.pdf", "pdf", 1, "b-key",
                    owner_id="owner-b",
                )

                exists, docs = database.get_chat_documents_for_owner(
                    ["doc-a", "doc-b"], "chat-a", "owner-a"
                )
                self.assertTrue(exists)
                self.assertEqual([doc["id"] for doc in docs], ["doc-a"])

                exists, docs = database.get_chat_documents_for_owner(
                    ["doc-a"], "chat-a", "owner-b"
                )
                self.assertFalse(exists)
                self.assertEqual(docs, [])
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
