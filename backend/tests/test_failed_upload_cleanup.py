import asyncio
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.routers import documents


class FailedUploadCleanupTests(unittest.TestCase):
    def test_expired_chunk_pipeline_failure_cleans_incomplete_upload(self):
        class Upload:
            filename = "retry.pdf"

            async def read(self, _limit):
                return b"validated PDF bytes"

            async def close(self):
                return None

        class Vector:
            @staticmethod
            def tobytes():
                return b"normalized-vector"

        async def run_upload():
            storage = Mock()
            storage.provider = "local"
            storage.locator.return_value = "local:upload-object"
            storage._path_for_key.return_value = Path("upload-object")
            storage.processing_path.return_value = nullcontext(Path("upload.pdf"))
            cleanup = AsyncMock()
            with patch.object(documents, "validate_file", return_value=(True, "")), patch.object(
                documents.database, "find_document_by_hash", return_value=None
            ), patch.object(documents.database, "create_document") as create_document, patch.object(
                documents.database,
                "add_chunks",
                side_effect=RuntimeError("HTTP status 404: stream not found"),
            ), patch.object(documents, "get_storage", return_value=storage), patch.object(
                documents, "process_document",
                return_value={
                    "chunks": [{"content": "text", "chunk_id": "chunk-a", "chunk_index": 0}],
                    "total_pages": 1,
                    "total_chunks": 1,
                },
            ), patch.object(documents, "generate_embeddings", return_value=[Vector()]), patch.object(
                documents, "cleanup_failed_upload", cleanup
            ):
                response = await documents.upload_documents(
                    files=[Upload()], conversation_id=None, user={"id": "owner-a"}
                )

            document_id = create_document.call_args.kwargs["document_id"]
            self.assertEqual(response["total_failed"], 1)
            cleanup.assert_awaited_once_with(
                document_id, "upload-object", "owner-a"
            )

        asyncio.run(run_upload())

    def test_turso_expired_stream_cleanup_retries_idempotent_record_delete(self):
        async def run_cleanup():
            with patch.object(
                documents.database,
                "delete_document",
                side_effect=[RuntimeError("HTTP 404: stream not found"), None],
            ) as delete_record, patch.object(documents.asyncio, "sleep") as sleep:
                await documents.cleanup_failed_upload("doc-1", None, "owner-1")
            self.assertEqual(delete_record.call_count, 2)
            delays = [call.args[0] for call in sleep.await_args_list if call.args and call.args[0] > 0]
            self.assertEqual(delays, [0.2])

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
            delays = [call.args[0] for call in sleep.await_args_list if call.args and call.args[0] > 0]
            self.assertEqual(delays, [0.2, 0.4])

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
            delays = [call.args[0] for call in sleep.await_args_list if call.args and call.args[0] > 0]
            self.assertEqual(delays, [0.2])

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
            delays = [call.args[0] for call in sleep.await_args_list if call.args and call.args[0] > 0]
            self.assertEqual(delays, [0.2, 0.4])

        asyncio.run(run_cleanup())


if __name__ == "__main__":
    unittest.main()
