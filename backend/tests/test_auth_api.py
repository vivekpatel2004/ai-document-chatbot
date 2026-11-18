"""API checks for authentication and tenant isolation (stdlib unittest)."""
import os
import hashlib
import io
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

_test_db = Path(tempfile.gettempdir()) / f"document-chat-auth-{os.getpid()}.sqlite3"
os.environ["DB_PATH"] = str(_test_db)
os.environ["DATABASE_PROVIDER"] = "sqlite"
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_AUTH_TOKEN"] = ""
os.environ["TURSO_AUTH_TOKEN"] = ""

import httpx

from app.main import app
from app.services import database


class AuthenticationApiTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    @classmethod
    def tearDownClass(cls):
        for path in (_test_db, Path(f"{_test_db}-wal"), Path(f"{_test_db}-shm")):
            path.unlink(missing_ok=True)

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_signup_login_and_current_user(self):
        response = await self.client.post("/api/auth/signup", json={
            "name": "Avery Example",
            "email": "avery@example.test",
            "password": "secure-password-123",
        })
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload["token"])
        self.assertEqual(payload["user"]["email"], "avery@example.test")

        me = await self.client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {payload['token']}"
        })
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["id"], payload["user"]["id"])

        login = await self.client.post("/api/auth/login", json={
            "email": "AVERY@example.test",
            "password": "secure-password-123",
        })
        self.assertEqual(login.status_code, 200)
        bad_login = await self.client.post("/api/auth/login", json={
            "email": "avery@example.test",
            "password": "incorrect-password",
        })
        self.assertEqual(bad_login.status_code, 401)

    async def test_private_conversations_and_logout(self):
        async def create_account(email: str):
            response = await self.client.post("/api/auth/signup", json={
                "name": email.split("@")[0], "email": email, "password": "secure-password-123"
            })
            self.assertEqual(response.status_code, 201)
            return response.json()

        alice = await create_account("alice@example.test")
        bob = await create_account("bob@example.test")
        alice_headers = {"Authorization": f"Bearer {alice['token']}"}
        bob_headers = {"Authorization": f"Bearer {bob['token']}"}

        denied = await self.client.get("/api/conversations/")
        self.assertEqual(denied.status_code, 401)
        created = await self.client.post("/api/conversations/", json={"title": "Private notes"}, headers=alice_headers)
        self.assertEqual(created.status_code, 201)
        conversation_id = created.json()["conversation"]["id"]
        document_id = "private-document-for-test"
        database.create_document(
            document_id=document_id,
            conversation_id=conversation_id,
            owner_id=alice["user"]["id"],
            filename="private.txt",
            file_type="txt",
            file_size=12,
            file_path="private-test-file.txt",
        )
        second_conversation = await self.client.post(
            "/api/conversations/", json={"title": "Another private chat"}, headers=alice_headers
        )
        second_conversation_id = second_conversation.json()["conversation"]["id"]
        self.assertEqual(
            database.attach_document_to_conversation(document_id, second_conversation_id, alice["user"]["id"]),
            1,
        )
        selected_documents = await self.client.get(
            f"/api/conversations/{second_conversation_id}/documents", headers=alice_headers
        )
        self.assertEqual([item["id"] for item in selected_documents.json()["documents"]], [document_id])

        bob_list = await self.client.get("/api/conversations/", headers=bob_headers)
        self.assertEqual(bob_list.status_code, 200)
        self.assertEqual(bob_list.json()["total"], 0)
        cross_account = await self.client.get(f"/api/conversations/{conversation_id}", headers=bob_headers)
        self.assertEqual(cross_account.status_code, 404)
        cross_document = await self.client.get(f"/api/documents/{document_id}", headers=bob_headers)
        self.assertEqual(cross_document.status_code, 404)
        self.assertNotIn("private-test-file.txt", cross_document.text)
        cross_messages = await self.client.get(f"/api/conversations/{conversation_id}/messages", headers=bob_headers)
        self.assertEqual(cross_messages.status_code, 404)

        database.add_message(
            message_id="chat-order-first",
            conversation_id=conversation_id,
            role="user",
            content="First question",
        )
        database.add_message(
            message_id="chat-order-second",
            conversation_id=conversation_id,
            role="assistant",
            content="First answer",
        )
        messages = await self.client.get(f"/api/conversations/{conversation_id}/messages", headers=alice_headers)
        self.assertEqual([item["content"] for item in messages.json()["messages"]], ["First question", "First answer"])

        removed = await self.client.delete(f"/api/conversations/{conversation_id}", headers=alice_headers)
        self.assertEqual(removed.status_code, 200)
        retained_document = await self.client.get(f"/api/documents/{document_id}", headers=alice_headers)
        self.assertEqual(retained_document.status_code, 200)
        self.assertIsNone(retained_document.json()["document"]["conversation_id"])
        retained_association = await self.client.get(
            f"/api/conversations/{second_conversation_id}/documents", headers=alice_headers
        )
        self.assertEqual(retained_association.status_code, 200)
        self.assertEqual([item["id"] for item in retained_association.json()["documents"]], [document_id])

        signed_out = await self.client.post("/api/auth/logout", headers=alice_headers)
        self.assertEqual(signed_out.status_code, 200)
        invalidated = await self.client.get("/api/auth/me", headers=alice_headers)
        self.assertEqual(invalidated.status_code, 401)

    async def test_text_markdown_pdf_exports_include_sources_and_check_owner(self):
        account = await self.client.post("/api/auth/signup", json={
            "name": "Export Owner", "email": "export@example.test", "password": "secure-password-123"
        })
        owner = account.json()
        owner_headers = {"Authorization": f"Bearer {owner['token']}"}
        other = await self.client.post("/api/auth/signup", json={
            "name": "Other User", "email": "other-export@example.test", "password": "secure-password-123"
        })
        other_headers = {"Authorization": f"Bearer {other.json()['token']}"}

        created = await self.client.post("/api/conversations/", json={"title": "Quarterly Review 2024"}, headers=owner_headers)
        conversation_id = created.json()["conversation"]["id"]
        database.add_message(
            message_id="export-user-message",
            conversation_id=conversation_id,
            role="user",
            content="What changed in the policy?",
        )
        database.add_message(
            message_id="export-answer-message",
            conversation_id=conversation_id,
            role="assistant",
            content="The policy changed in March.",
            sources=[
                {"document_id": "policy-id", "filename": "policy.pdf", "page_number": 3, "chunk_index": 0},
                {"document_id": "policy-id", "filename": "policy.pdf", "page_number": 3, "chunk_index": 1},
                {"document_id": "policy-id", "filename": "policy.pdf", "page_number": 4, "chunk_index": 2},
            ],
        )

        for export_format in ("txt", "md", "pdf"):
            response = await self.client.post(
                f"/api/export/{export_format}",
                json={"conversation_id": conversation_id, "format": export_format, "include_sources": True},
                headers=owner_headers,
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(f"Quarterly_Review_2024.{export_format}".encode(), response.headers["content-disposition"].encode())
            if export_format == "pdf":
                import fitz
                pdf = fitz.open(stream=response.content, filetype="pdf")
                exported_text = "\n".join(page.get_text() for page in pdf)
                self.assertIn("What changed in the policy?", exported_text)
                self.assertIn("policy.pdf (p. 3)", exported_text)
                self.assertIn("policy.pdf (p. 4)", exported_text)
                self.assertIn("[1] policy.pdf (p. 3)", exported_text)
            else:
                exported_text = response.text
                self.assertIn("What changed in the policy?", exported_text)
                self.assertIn("policy.pdf (p. 3)", exported_text)
                self.assertIn("policy.pdf (p. 4)", exported_text)
                self.assertIn("[1] policy.pdf (p. 3)", exported_text)

        hidden = await self.client.post(
            "/api/export/pdf",
            json={"conversation_id": conversation_id, "format": "pdf"},
            headers=other_headers,
        )
        self.assertEqual(hidden.status_code, 404)

        empty = await self.client.post("/api/conversations/", json={"title": "Empty conversation"}, headers=owner_headers)
        empty_export = await self.client.post(
            "/api/export/txt",
            json={"conversation_id": empty.json()["conversation"]["id"], "format": "txt"},
            headers=owner_headers,
        )
        self.assertEqual(empty_export.status_code, 400)

    async def test_invalid_document_does_not_create_a_chat(self):
        account = await self.client.post("/api/auth/signup", json={
            "name": "Chat Owner", "email": "chat-owner@example.test", "password": "secure-password-123"
        })
        headers = {"Authorization": f"Bearer {account.json()['token']}"}
        before = await self.client.get("/api/conversations/", headers=headers)

        response = await self.client.post("/api/chat/", json={
            "message": "What is this document about?",
            "document_ids": ["missing-document"],
            "stream": False,
        }, headers=headers)

        self.assertEqual(response.status_code, 404)
        after = await self.client.get("/api/conversations/", headers=headers)
        self.assertEqual(after.json()["total"], before.json()["total"])

    async def test_chat_retrieves_tagged_gemini_vectors_and_saves_answer(self):
        import numpy as np

        account = await self.client.post("/api/auth/signup", json={
            "name": "RAG User", "email": "rag-chat@example.test",
            "password": "secure-password-123",
        })
        owner_id = account.json()["user"]["id"]
        headers = {"Authorization": f"Bearer {account.json()['token']}"}
        document_id = str(uuid.uuid4())
        database.create_document(
            document_id=document_id, conversation_id=None, owner_id=owner_id,
            filename="rag.txt", file_type="txt", file_size=20,
            file_path="rag.txt",
        )
        database.update_document_status(document_id, "processed", 0, 1)
        vector = np.zeros(768, dtype=np.float32)
        vector[0] = 1.0
        database.add_chunk(
            "rag-chat-chunk", document_id, 0, "The answer is in this passage.",
            embedding=vector.tobytes(),
            embedding_model="gemini-embedding-001",
        )
        llm = AsyncMock(return_value={"content": "The answer is in this passage."})
        with patch("app.services.rag_service.create_embedding", return_value=vector), patch(
            "app.routers.chat._get_llm_service", return_value=llm
        ):
            response = await self.client.post("/api/chat/", json={
                "message": "What is the answer?", "document_ids": [document_id],
                "stream": False,
            }, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["message"], "The answer is in this passage.")
        self.assertEqual(response.json()["sources"][0]["document_id"], document_id)
        self.assertEqual(
            database.get_messages(response.json()["conversation_id"])[-1]["content"],
            response.json()["message"],
        )

    async def test_duplicate_upload_reuses_owned_document(self):
        account = await self.client.post("/api/auth/signup", json={
            "name": "Upload Owner", "email": "upload-owner@example.test", "password": "secure-password-123"
        })
        headers = {"Authorization": f"Bearer {account.json()['token']}"}
        owner_id = account.json()["user"]["id"]
        conversation = await self.client.post(
            "/api/conversations/", json={"title": "Existing file"}, headers=headers
        )
        conversation_id = conversation.json()["conversation"]["id"]
        content = b"A previously uploaded text document with useful content."
        document_id = "existing-upload-document"
        database.create_document(
            document_id=document_id,
            conversation_id=None,
            owner_id=owner_id,
            content_hash=hashlib.sha256(content).hexdigest(),
            filename="notes.txt",
            file_type="txt",
            file_size=len(content),
            file_path="existing-upload.txt",
        )
        database.update_document_status(document_id, "processed", total_pages=1, total_chunks=1)

        response = await self.client.post(
            "/api/documents/upload",
            data={"conversation_id": conversation_id},
            files={"files": ("notes.txt", content, "text/plain")},
            headers=headers,
        )
        self.assertEqual(response.status_code, 201)
        result = response.json()
        self.assertEqual(result["total_uploaded"], 1)
        self.assertEqual(result["documents"][0]["id"], document_id)
        self.assertEqual(len(database.list_documents(owner_id=owner_id)), 1)
        self.assertEqual(
            [item["id"] for item in database.list_documents(conversation_id, owner_id)],
            [document_id],
        )

    async def test_document_file_operations_require_ownership(self):
        import io
        from app.services.storage import R2Storage

        owner = await self.client.post("/api/auth/signup", json={
            "name": "Storage Owner", "email": "storage-owner@example.test",
            "password": "secure-password-123",
        })
        other = await self.client.post("/api/auth/signup", json={
            "name": "Storage Other", "email": "storage-other@example.test",
            "password": "secure-password-123",
        })
        owner_headers = {"Authorization": f"Bearer {owner.json()['token']}"}
        other_headers = {"Authorization": f"Bearer {other.json()['token']}"}
        document_id = "private-file-operation-test"
        database.create_document(
            document_id=document_id,
            conversation_id=None,
            owner_id=owner.json()["user"]["id"],
            filename="private.txt",
            file_type="txt",
            file_size=7,
            file_path="r2:documents/private-file-operation-test/document.txt",
        )

        client = Mock()
        client.get_object.return_value = {"Body": io.BytesIO(b"private")}
        storage = R2Storage(client, "private-test-bucket")
        with patch("app.routers.documents.storage_for_locator", return_value=storage):
            denied_download = await self.client.get(
                f"/api/documents/{document_id}/download", headers=other_headers
            )
            self.assertEqual(denied_download.status_code, 404)
            client.get_object.assert_not_called()

            allowed_download = await self.client.get(
                f"/api/documents/{document_id}/download", headers=owner_headers
            )
            self.assertEqual(allowed_download.status_code, 200)
            self.assertEqual(allowed_download.content, b"private")
            self.assertIn("attachment", allowed_download.headers["content-disposition"])
            client.get_object.assert_called_once_with(
                Bucket="private-test-bucket",
                Key="documents/private-file-operation-test/document.txt",
            )

            denied_delete = await self.client.delete(
                f"/api/documents/{document_id}", headers=other_headers
            )
            self.assertEqual(denied_delete.status_code, 404)
            client.delete_object.assert_not_called()

            allowed_delete = await self.client.delete(
                f"/api/documents/{document_id}", headers=owner_headers
            )
            self.assertEqual(allowed_delete.status_code, 200)
            client.delete_object.assert_called_once_with(
                Bucket="private-test-bucket",
                Key="documents/private-file-operation-test/document.txt",
            )
        self.assertIsNone(database.get_document_for_owner(document_id, owner.json()["user"]["id"]))

    async def test_document_storage_failures_return_safe_error(self):
        owner = await self.client.post("/api/auth/signup", json={
            "name": "Storage Failure", "email": "storage-failure@example.test",
            "password": "secure-password-123",
        })
        headers = {"Authorization": f"Bearer {owner.json()['token']}"}
        document_id = "storage-failure-test"
        database.create_document(
            document_id=document_id,
            conversation_id=None,
            owner_id=owner.json()["user"]["id"],
            filename="failure.txt",
            file_type="txt",
            file_size=4,
            file_path="r2:documents/storage-failure-test/document.txt",
        )
        from app.services.storage import StorageError
        storage = Mock()
        storage.read.side_effect = StorageError("R2 request failed; secret omitted")
        with patch("app.routers.documents.storage_for_locator", return_value=storage):
            response = await self.client.get(
                f"/api/documents/{document_id}/download", headers=headers
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", response.text.lower())
        self.assertIsNotNone(database.get_document_for_owner(document_id, owner.json()["user"]["id"]))

        from app.services.storage import R2Storage
        delete_document_id = "storage-delete-failure-test"
        database.create_document(
            document_id=delete_document_id,
            conversation_id=None,
            owner_id=owner.json()["user"]["id"],
            filename="delete-failure.txt",
            file_type="txt",
            file_size=4,
            file_path="r2:documents/storage-delete-failure-test/document.txt",
        )
        client = Mock()
        client.delete_object.side_effect = RuntimeError("private-key-value")
        storage = R2Storage(client, "private-test-bucket")
        with patch("app.routers.documents.storage_for_locator", return_value=storage):
            response = await self.client.delete(
                f"/api/documents/{delete_document_id}", headers=headers
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-key-value", response.text)
        self.assertIsNotNone(database.get_document_for_owner(
            delete_document_id, owner.json()["user"]["id"]
        ))

    async def test_upload_uses_local_and_r2_storage_adapters(self):
        import numpy as np
        from app.services.storage import LocalStorage, R2Storage

        account = await self.client.post("/api/auth/signup", json={
            "name": "Upload Storage", "email": "upload-storage@example.test",
            "password": "secure-password-123",
        })
        headers = {"Authorization": f"Bearer {account.json()['token']}"}

        class MemoryR2:
            def __init__(self):
                self.objects = {}

            def put_object(self, **kwargs):
                self.objects[kwargs["Key"]] = kwargs["Body"]

            def get_object(self, **kwargs):
                return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}

            def delete_object(self, **kwargs):
                self.objects.pop(kwargs["Key"], None)

        with tempfile.TemporaryDirectory(prefix="document-upload-local-") as local_root:
            providers = [
                ("local", LocalStorage(local_root)),
                ("r2", R2Storage(MemoryR2(), "private-test-bucket")),
            ]
            with patch("app.routers.documents.process_document", side_effect=lambda path: {
                "chunks": [{
                    "chunk_id": str(uuid.uuid4()), "chunk_index": 0,
                    "page_number": None, "content": Path(path).read_text(encoding="utf-8"),
                }],
                "total_pages": 0, "total_chunks": 1,
            }), patch(
                "app.routers.documents.generate_embeddings",
                return_value=np.asarray([[1.0, 0.0]], dtype=np.float32),
            ):
                for provider, storage in providers:
                    content = f"Storage integration {provider}".encode()
                    with self.subTest(provider=provider), patch(
                        "app.routers.documents.get_storage", return_value=storage
                    ):
                        response = await self.client.post(
                            "/api/documents/upload",
                            files={"files": (f"{provider}.txt", content, "text/plain")},
                            headers=headers,
                        )
                        self.assertEqual(response.status_code, 201)
                        document_id = response.json()["documents"][0]["id"]
                        row = database.get_document_for_owner(
                            document_id, account.json()["user"]["id"]
                        )
                        stored_chunk = database.get_document_chunks(document_id)[0]
                        self.assertEqual(
                            stored_chunk["embedding_model"], "gemini-embedding-001"
                        )
                        self.assertEqual(
                            stored_chunk["embedding"],
                            np.asarray([1.0, 0.0], dtype=np.float32).tobytes(),
                        )
                        if provider == "r2":
                            self.assertTrue(row["file_path"].startswith("r2:documents/"))
                            self.assertNotIn("http", row["file_path"])
                        else:
                            self.assertTrue(Path(row["file_path"]).is_relative_to(Path(local_root).resolve()))
                        self.assertNotIn("file_path", response.json()["documents"][0])


if __name__ == "__main__":
    unittest.main()
