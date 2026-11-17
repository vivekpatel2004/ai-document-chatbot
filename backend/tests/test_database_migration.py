"""Verify auth schema migration preserves an existing SQLite database."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_db_path = Path(tempfile.gettempdir()) / f"document-chat-migration-{os.getpid()}.sqlite3"
os.environ["DB_PATH"] = str(_db_path)
os.environ["DATABASE_PROVIDER"] = "sqlite"
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_AUTH_TOKEN"] = ""
os.environ["TURSO_AUTH_TOKEN"] = ""

class ExistingDatabaseMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with sqlite3.connect(str(_db_path)) as conn:
            conn.executescript("""
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT 'New Chat',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY, conversation_id TEXT, filename TEXT NOT NULL,
                    file_type TEXT NOT NULL, file_size INTEGER NOT NULL DEFAULT 0,
                    file_path TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'processing',
                    total_pages INTEGER NOT NULL DEFAULT 0, total_chunks INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
                );
                CREATE TABLE chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL, page_number INTEGER, embedding BLOB,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                INSERT INTO conversations (id, title) VALUES ('legacy-chat', 'Legacy chat');
                INSERT INTO documents (id, conversation_id, filename, file_type, file_path)
                    VALUES ('legacy-doc', 'legacy-chat', 'notes.txt', 'txt', 'uploads/notes.txt');
                INSERT INTO chunks (id, document_id, chunk_index, content)
                    VALUES ('legacy-chunk', 'legacy-doc', 0, 'preserved text');
                INSERT INTO messages (id, conversation_id, role, content)
                    VALUES ('legacy-message', 'legacy-chat', 'user', 'preserved message');
            """)
        conn.close()
        env = os.environ.copy()
        env["DB_PATH"] = str(_db_path)
        worker = "from app.services.database import init_db; init_db(); init_db()"
        subprocess.run([sys.executable, "-c", worker], cwd=Path(__file__).resolve().parents[1], env=env, check=True)

    @classmethod
    def tearDownClass(cls):
        for path in (_db_path, Path(f"{_db_path}-wal"), Path(f"{_db_path}-shm")):
            path.unlink(missing_ok=True)

    def test_existing_rows_remain_and_unowned(self):
        with sqlite3.connect(str(_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            self.assertIsNone(conn.execute("SELECT owner_id FROM conversations WHERE id='legacy-chat'").fetchone()[0])
            self.assertIsNone(conn.execute("SELECT owner_id FROM documents WHERE id='legacy-doc'").fetchone()[0])
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"users", "sessions"}.issubset(tables))
        conn.close()


if __name__ == "__main__":
    unittest.main()
