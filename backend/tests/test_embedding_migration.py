"""Read-only dry-run and resumable batch behavior for vector migration."""

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.services import database, db_connection
from scripts import reembed_gemini_embeddings


class EmbeddingMigrationJobTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="embedding-migration-")
        self.db_path = Path(self.temp_dir.name) / "migration.sqlite3"
        self.env_patch = patch.dict(os.environ, {
            "DATABASE_PROVIDER": "sqlite",
            "DATABASE_URL": "",
            "DATABASE_AUTH_TOKEN": "",
            "TURSO_AUTH_TOKEN": "",
        })
        self.env_patch.start()
        self.path_patch = patch.object(db_connection, "DB_PATH", self.db_path)
        self.path_patch.start()
        database.init_db()
        self.document_id = str(uuid.uuid4())
        database.create_document(
            document_id=self.document_id,
            conversation_id=None,
            filename="migration.txt",
            file_type="txt",
            file_size=10,
            file_path="migration.txt",
        )

    def tearDown(self):
        self.path_patch.stop()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def _add_pending_chunks(self, count):
        for index in range(count):
            database.add_chunk(
                f"migration-chunk-{index}-{uuid.uuid4()}",
                self.document_id,
                index,
                f"Migration passage {index}",
                embedding=b"legacy-vector",
            )

    def test_dry_run_does_not_change_vectors_or_schema(self):
        self._add_pending_chunks(1)
        before = database.get_document_chunks(self.document_id)[0]["embedding"]

        result = reembed_gemini_embeddings.main(["--dry-run"])

        after = database.get_document_chunks(self.document_id)[0]["embedding"]
        self.assertEqual(result, 0)
        self.assertEqual(after, before)
        self.assertIsNone(database.get_document_chunks(self.document_id)[0]["embedding_model"])

    def test_failed_batch_is_reported_and_rerun_completes_remaining_rows(self):
        self._add_pending_chunks(3)
        vector = np.zeros(768, dtype=np.float32)
        vector[0] = 1.0
        batch_results = [RuntimeError("simulated provider outage"),
                         np.asarray([vector]), np.asarray([vector])]
        with patch.object(
            reembed_gemini_embeddings,
            "generate_embeddings",
            side_effect=batch_results,
        ):
            first_result = reembed_gemini_embeddings.main(["--batch-size", "1"])

        self.assertEqual(first_result, 1)
        self.assertEqual(database.get_embedding_migration_counts()["pending"], 1)

        with patch.object(
            reembed_gemini_embeddings,
            "generate_embeddings",
            return_value=np.asarray([vector]),
        ):
            second_result = reembed_gemini_embeddings.main(["--batch-size", "1"])

        self.assertEqual(second_result, 0)
        self.assertEqual(database.get_embedding_migration_counts()["pending"], 0)


if __name__ == "__main__":
    unittest.main()
