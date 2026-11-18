import asyncio
import unittest
from unittest.mock import Mock, patch

from app.routers import documents


class FailedUploadCleanupTests(unittest.TestCase):
    def test_turso_expired_stream_cleanup_retries_idempotent_record_delete(self):
        async def run_cleanup():
            with patch.object(
                documents.database,
                "delete_document",
                side_effect=[RuntimeError("HTTP 404: stream not found"), None],
            ) as delete_record, patch.object(documents.asyncio, "sleep") as sleep:
                await documents.cleanup_failed_upload("doc-1", None, "owner-1")
            self.assertEqual(delete_record.call_count, 2)
            sleep.assert_awaited_once_with(0.2)

        asyncio.run(run_cleanup())

    def test_cleanup_stops_after_bounded_stream_failures(self):
        async def run_cleanup():
            with patch.object(
                documents.database,
                "delete_document",
                side_effect=RuntimeError("HTTP 404: stream not found"),
            ) as delete_record, patch.object(documents.asyncio, "sleep") as sleep:
                await documents.cleanup_failed_upload("doc-2", None, "owner-2")
            self.assertEqual(delete_record.call_count, 3)
            self.assertEqual(sleep.await_count, 2)

        asyncio.run(run_cleanup())

    def test_non_stream_cleanup_errors_are_not_retried(self):
        async def run_cleanup():
            with patch.object(
                documents.database,
                "delete_document",
                side_effect=RuntimeError("permission denied"),
            ) as delete_record, patch.object(documents.asyncio, "sleep") as sleep:
                await documents.cleanup_failed_upload("doc-3", None, "owner-3")
            delete_record.assert_called_once_with("doc-3", "owner-3")
            sleep.assert_not_awaited()

        asyncio.run(run_cleanup())

    def test_object_delete_retry_keeps_database_record_until_object_is_removed(self):
        async def run_cleanup():
            storage = Mock()
            storage.delete.side_effect = [RuntimeError("temporary B2 error"), None]
            with patch.object(documents, "storage_for_locator", return_value=storage), patch.object(
                documents.database, "delete_document", return_value=1
            ) as delete_record, patch.object(documents.asyncio, "sleep") as sleep:
                await documents.cleanup_failed_upload(
                    "doc-4", "r2:documents/doc-4/file.pdf", "owner-4"
                )
            self.assertEqual(storage.delete.call_count, 2)
            delete_record.assert_called_once_with("doc-4", "owner-4")
            sleep.assert_awaited_once_with(0.2)

        asyncio.run(run_cleanup())

    def test_object_delete_failure_preserves_database_recovery_record(self):
        async def run_cleanup():
            storage = Mock()
            storage.delete.side_effect = RuntimeError("temporary B2 error")
            with patch.object(documents, "storage_for_locator", return_value=storage), patch.object(
                documents.database, "delete_document"
            ) as delete_record, patch.object(documents.asyncio, "sleep") as sleep:
                await documents.cleanup_failed_upload(
                    "doc-5", "r2:documents/doc-5/file.pdf", "owner-5"
                )
            self.assertEqual(storage.delete.call_count, 3)
            delete_record.assert_not_called()
            self.assertEqual(sleep.await_count, 2)

        asyncio.run(run_cleanup())


if __name__ == "__main__":
    unittest.main()
