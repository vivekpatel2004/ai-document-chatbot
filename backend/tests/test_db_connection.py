import os
import asyncio
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import db_connection
from app.services.db_connection import get_connection
from app.services import database


class DatabaseConnectionTests(unittest.TestCase):
    def test_safe_turso_connection_target_contains_only_hostname(self):
        with patch.dict(os.environ, {
            "DATABASE_PROVIDER": "turso",
            "DATABASE_URL": "libsql://url-user:url-password@safe-db.turso.io/path?token=url-secret",
            "DATABASE_AUTH_TOKEN": "separate-secret",
        }):
            provider, target, source = db_connection.connection_target()
        self.assertEqual((provider, target, source), ("Turso", "safe-db.turso.io", "DATABASE_URL"))
        self.assertNotIn("secret", target)
        self.assertNotIn("password", target)

    def test_sqlite_connection_target_reports_path_source(self):
        path = Path(tempfile.gettempdir()) / "chatbot-target.sqlite3"
        with patch.object(db_connection, "DB_PATH", path):
            with patch.dict(os.environ, {
                "DATABASE_PROVIDER": "sqlite",
                "DATABASE_URL": "",
                "DB_PATH": str(path),
            }):
                provider, target, source = db_connection.connection_target()
        self.assertEqual(provider, "SQLite")
        self.assertEqual(Path(target), path.resolve())
        self.assertEqual(source, "DB_PATH")

    def test_database_url_selects_sqlite_file(self):
        path = Path(tempfile.gettempdir()) / f"database-url-{os.getpid()}.sqlite3"
        path.unlink(missing_ok=True)
        try:
            with patch.dict(os.environ, {
                "DATABASE_PROVIDER": "sqlite",
                "DATABASE_URL": str(path),
            }):
                with get_connection() as connection:
                    connection.execute("CREATE TABLE provider_check (value TEXT)")
                    connection.execute("INSERT INTO provider_check VALUES (?)", ("ok",))
                self.assertTrue(path.is_file())
                with get_connection() as connection:
                    self.assertEqual(
                        connection.execute("SELECT value FROM provider_check").fetchone()[0],
                        "ok",
                    )
        finally:
            path.unlink(missing_ok=True)

    def test_turso_requires_url_and_backend_token(self):
        with patch.dict(os.environ, {
            "DATABASE_PROVIDER": "turso",
            "DATABASE_URL": "",
            "DATABASE_AUTH_TOKEN": "",
            "TURSO_AUTH_TOKEN": "",
        }):
            with self.assertRaisesRegex(ValueError, "DATABASE_URL is required"):
                with get_connection():
                    pass

        with patch.dict(os.environ, {
            "DATABASE_PROVIDER": "turso",
            "DATABASE_URL": "libsql://example.turso.io",
            "DATABASE_AUTH_TOKEN": "",
            "TURSO_AUTH_TOKEN": "",
        }):
            with self.assertRaisesRegex(ValueError, "DATABASE_AUTH_TOKEN"):
                with get_connection():
                    pass

    def test_turso_provider_uses_dbapi_connection_contract(self):
        path = Path(tempfile.gettempdir()) / f"turso-dbapi-contract-{os.getpid()}.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        calls = []

        def connect(url, auth_token):
            calls.append((url, auth_token))
            return sqlite3.connect(str(path))

        driver = types.SimpleNamespace(connect=connect)
        try:
            with patch.dict(sys.modules, {"turso_serverless": driver}):
                with patch.dict(os.environ, {
                    "DATABASE_PROVIDER": "turso",
                    "DATABASE_URL": "libsql://db.example.test",
                    "DATABASE_AUTH_TOKEN": "test-token",
                }):
                    database.init_db()
                    database.execute(
                        "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
                        ("provider-user", "Test", "provider@example.test", "hash"),
                    )
                    database.create_conversation("provider-chat", owner_id="provider-user")
                    database.create_document(
                        document_id="provider-document",
                        conversation_id="provider-chat",
                        owner_id="provider-user",
                        filename="provider.txt",
                        file_type="txt",
                        file_size=12,
                        file_path="provider.txt",
                    )
                    database.add_chunk(
                        "provider-chunk", "provider-document", 0,
                        "persisted chunk", page_number=1,
                        embedding=b"\x00\x01embedding",
                    )
                    database.add_message("provider-message", "provider-chat", "user", "Check provider")
                    self.assertEqual(
                        database.get_messages("provider-chat")[0]["content"],
                        "Check provider",
                    )
                    self.assertEqual(
                        database.get_chunks_for_documents(["provider-document"])[0]["embedding"],
                        b"\x00\x01embedding",
                    )
                    self.assertEqual(
                        database.list_documents("provider-chat", "provider-user")[0]["id"],
                        "provider-document",
                    )
            self.assertTrue(calls)
            self.assertEqual(calls[0], ("libsql://db.example.test", "test-token"))
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{path}{suffix}").unlink(missing_ok=True)

    def test_request_scope_reuses_connection_and_rolls_back_failed_operation(self):
        raw = sqlite3.connect(":memory:")
        opened = []

        class TrackedConnection:
            def __init__(self, connection):
                self.connection = connection
                self.commits = 0
                self.rollbacks = 0
                self.closed = False

            def execute(self, *args, **kwargs):
                return self.connection.execute(*args, **kwargs)

            def commit(self):
                self.commits += 1
                self.connection.commit()

            def rollback(self):
                self.rollbacks += 1
                self.connection.rollback()

            def close(self):
                self.closed = True
                self.connection.close()

        tracked = TrackedConnection(raw)
        with patch.dict(os.environ, {"DATABASE_PROVIDER": "sqlite"}):
            with patch.object(db_connection, "_open_connection", return_value=tracked):
                with db_connection.request_connection_scope():
                    with get_connection() as conn:
                        first = conn
                        conn.execute("CREATE TABLE values_table (value TEXT)")
                    try:
                        with get_connection() as conn:
                            self.assertIs(conn, first)
                            conn.execute("INSERT INTO values_table VALUES ('rolled back')")
                            raise RuntimeError("abort transaction")
                    except RuntimeError:
                        pass
                    with get_connection() as conn:
                        self.assertIs(conn, first)
                        self.assertEqual(conn.execute("SELECT COUNT(*) FROM values_table").fetchone()[0], 0)

        self.assertEqual(tracked.commits, 2)
        self.assertEqual(tracked.rollbacks, 1)
        self.assertTrue(tracked.closed)

    def test_turso_request_reuses_one_connection_across_worker_calls(self):
        from starlette.concurrency import run_in_threadpool

        class TrackedConnection:
            def __init__(self):
                self.closed = False

            def execute(self, *args, **kwargs):
                return self

            def fetchone(self):
                return (1,)

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                self.closed = True

        async def exercise_scope():
            seen = []

            def query():
                with get_connection() as active:
                    seen.append(active)
                    active.execute("SELECT 1").fetchone()

            with db_connection.request_connection_scope():
                await run_in_threadpool(query)
                await run_in_threadpool(query)
            return seen

        tracked = TrackedConnection()
        opened = []

        def open_connection():
            opened.append(tracked)
            return tracked

        with patch.dict(os.environ, {"DATABASE_PROVIDER": "turso"}), patch.object(
            db_connection, "_open_connection", side_effect=open_connection
        ):
            seen = asyncio.run(exercise_scope())
        self.assertEqual(len(opened), 1)
        self.assertEqual(len(seen), 2)
        self.assertIs(seen[0], seen[1])
        self.assertTrue(tracked.closed)

    def test_add_chunks_batches_multivalue_inserts_in_one_transaction(self):
        path = Path(tempfile.gettempdir()) / f"chatbot-batch-{os.getpid()}.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        statements = []
        wrappers = []

        class RecordingConnection:
            def __init__(self, connection):
                self.connection = connection
                self.commits = 0

            def execute(self, query, params=()):
                statements.append(query)
                return self.connection.execute(query, params)

            def commit(self):
                self.commits += 1
                self.connection.commit()

            def rollback(self):
                self.connection.rollback()

            def close(self):
                self.connection.close()

        def open_recording_connection():
            connection = RecordingConnection(sqlite3.connect(str(path)))
            wrappers.append(connection)
            return connection

        chunks = [
            {"chunk_id": f"batch-{index}", "chunk_index": index,
             "content": f"chunk content {index}", "page_number": index + 1}
            for index in range(120)
        ]
        try:
            with patch.dict(os.environ, {
                "DATABASE_PROVIDER": "sqlite", "DATABASE_URL": "", "DB_PATH": str(path),
            }):
                with patch.object(db_connection, "DB_PATH", path):
                    # Initialize schema with the real adapter, then trace only chunk inserts.
                    database.init_db()
                    database.create_document(
                        document_id="batch-document", conversation_id=None,
                        owner_id=None, filename="batch.txt", file_type="txt",
                        file_size=10, file_path="batch.txt",
                    )
                    statements.clear()
                    with patch.object(db_connection, "_open_connection", side_effect=open_recording_connection):
                        database.add_chunks("batch-document", chunks, [b"vector"] * len(chunks))
                    inserts = [query for query in statements if "INSERT INTO chunks" in query]
                    self.assertEqual(len(inserts), 3)
                    self.assertEqual([query.count("(?, ?, ?, ?, ?, ?, ?)") for query in inserts], [50, 50, 20])
                    self.assertEqual(sum(conn.commits for conn in wrappers), 1)
                    self.assertEqual(len(database.get_document_chunks("batch-document")), 120)
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{path}{suffix}").unlink(missing_ok=True)

    def test_document_metadata_cache_is_user_scoped_and_invalidated(self):
        path = Path(tempfile.gettempdir()) / f"chatbot-cache-{os.getpid()}.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        try:
            with patch.dict(os.environ, {
                "DATABASE_PROVIDER": "sqlite", "DATABASE_URL": "", "DB_PATH": str(path),
            }), patch.object(db_connection, "DB_PATH", path):
                database._invalidate_document_cache()
                database.init_db()
                database.create_document(
                    "cache-document", None, "cache.txt", "txt", 4, "cache.txt",
                    owner_id="owner-a",
                )
                self.assertEqual(database.get_document_for_owner("cache-document", "owner-a")["filename"], "cache.txt")
                self.assertIsNone(database.get_document_for_owner("cache-document", "owner-b"))
                self.assertEqual(database.list_documents(owner_id="owner-a")[0]["id"], "cache-document")
                self.assertEqual(database.list_documents(owner_id="owner-b"), [])

                database.create_document(
                    "cache-document-2", None, "new.txt", "txt", 3, "new.txt",
                    owner_id="owner-a",
                )
                self.assertEqual(len(database.list_documents(owner_id="owner-a")), 2)
                self.assertEqual(database.delete_document("cache-document", "owner-b"), 0)
                self.assertEqual(database.delete_document("cache-document", "owner-a"), 1)
                self.assertIsNone(database.get_document_for_owner("cache-document", "owner-a"))
                self.assertEqual(len(database.list_documents(owner_id="owner-a")), 1)
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{path}{suffix}").unlink(missing_ok=True)

    def test_bulk_document_attachment_is_idempotent_and_owner_scoped(self):
        path = Path(tempfile.gettempdir()) / f"chatbot-attach-{os.getpid()}.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        try:
            with patch.dict(os.environ, {
                "DATABASE_PROVIDER": "sqlite", "DATABASE_URL": "", "DB_PATH": str(path),
            }), patch.object(db_connection, "DB_PATH", path):
                database.init_db()
                database.execute(
                    "INSERT INTO users (id,name,email,password_hash) VALUES (?,?,?,?)",
                    ("attach-owner", "Owner", "attach-owner@example.test", "hash"),
                )
                database.execute(
                    "INSERT INTO users (id,name,email,password_hash) VALUES (?,?,?,?)",
                    ("attach-other", "Other", "attach-other@example.test", "hash"),
                )
                database.create_conversation("attach-chat", owner_id="attach-owner")
                database.create_document(
                    "attach-owned", None, "owned.txt", "txt", 1, "owned.txt",
                    owner_id="attach-owner",
                )
                database.create_document(
                    "attach-private", None, "private.txt", "txt", 1, "private.txt",
                    owner_id="attach-other",
                )
                expected = {"attach-owned"}
                self.assertEqual(
                    database.attach_documents_to_conversation(
                        ["attach-owned", "attach-private", "attach-owned"],
                        "attach-chat", "attach-owner",
                    ),
                    expected,
                )
                self.assertEqual(
                    database.attach_documents_to_conversation(
                        ["attach-owned"], "attach-chat", "attach-owner"
                    ),
                    expected,
                )
                self.assertEqual(
                    [row["id"] for row in database.list_documents("attach-chat", "attach-owner")],
                    ["attach-owned"],
                )
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{path}{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
