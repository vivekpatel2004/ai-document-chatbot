"""Isolated tests for the private local and Cloudflare R2 storage adapters."""

import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.storage import (
    LocalStorage,
    R2Storage,
    StorageConfigurationError,
    StorageError,
    build_document_key,
    clear_storage_cache,
    get_storage,
    storage_for_locator,
)


class FakeR2Client:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail_operation = None

    def put_object(self, **kwargs):
        self.calls.append(("put", kwargs))
        if self.fail_operation == "put":
            raise RuntimeError("fake secret access-key payload")
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
        return {}

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        if self.fail_operation == "get":
            raise RuntimeError("fake secret access-key payload")
        return {"Body": io.BytesIO(self.objects[(kwargs["Bucket"], kwargs["Key"])])}

    def delete_object(self, **kwargs):
        self.calls.append(("delete", kwargs))
        if self.fail_operation == "delete":
            raise RuntimeError("fake secret access-key payload")
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)
        return {}


class DocumentStorageTests(unittest.TestCase):
    def tearDown(self):
        clear_storage_cache()

    def test_local_put_read_delete_and_private_root(self):
        with tempfile.TemporaryDirectory(prefix="document-storage-") as folder:
            storage = LocalStorage(folder)
            key = build_document_key("11111111-2222-3333-4444-555555555555", ".txt")
            locator = storage.locator(key)
            self.assertEqual(storage.put(key, b"local private content"), locator)
            self.assertEqual(storage.read(locator), b"local private content")
            self.assertTrue(Path(locator).is_relative_to(Path(folder).resolve()))
            storage.delete(locator)
            with self.assertRaises(FileNotFoundError):
                storage.read(locator)

    def test_local_rejects_traversal_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="document-storage-") as folder:
            storage = LocalStorage(folder)
            for key in ("../escape.txt", "/absolute.txt", "documents\\escape.txt", "documents//empty.txt"):
                with self.subTest(key=key), self.assertRaises(StorageError):
                    storage.locator(key)
            key = "documents/11111111-2222-3333-4444-555555555555/document.txt"
            storage.put(key, b"first")
            with self.assertRaises(StorageError):
                storage.put(key, b"replacement")
            self.assertEqual(storage.read(storage.locator(key)), b"first")

    def test_r2_put_get_delete_use_private_bucket_and_object_key(self):
        client = FakeR2Client()
        storage = R2Storage(client, "private-bucket")
        key = "documents/11111111-2222-3333-4444-555555555555/document.pdf"
        locator = storage.put(key, b"pdf-bytes", "application/pdf")
        self.assertEqual(locator, f"r2:{key}")
        self.assertEqual(storage.read(locator), b"pdf-bytes")
        storage.delete(locator)
        self.assertNotIn(("private-bucket", key), client.objects)
        put_args = client.calls[0][1]
        self.assertNotIn("ACL", put_args)  # R2 bucket objects remain private.
        self.assertEqual(put_args["Bucket"], "private-bucket")
        self.assertEqual(put_args["Key"], key)

    def test_r2_failure_does_not_leak_provider_exception_details(self):
        client = FakeR2Client()
        storage = R2Storage(client, "private-bucket")
        client.fail_operation = "put"
        with self.assertRaises(StorageError) as error:
            storage.put("documents/test/document.txt", b"data")
        self.assertNotIn("test-secret-value", str(error.exception))
        self.assertNotIn("access-key", str(error.exception).lower())

        client = FakeR2Client()
        storage = R2Storage(client, "private-bucket")
        client.fail_operation = "get"
        with self.assertRaises(StorageError) as error:
            storage.read("r2:documents/test/document.txt")
        self.assertNotIn("fake secret access-key payload", str(error.exception))

        client = FakeR2Client()
        storage = R2Storage(client, "private-bucket")
        client.fail_operation = "delete"
        with self.assertRaises(StorageError) as error:
            storage.delete("r2:documents/test/document.txt")
        self.assertNotIn("fake secret access-key payload", str(error.exception))

    def test_r2_configuration_requires_credentials_without_printing_values(self):
        clear_storage_cache()
        with patch.dict(os.environ, {
            "STORAGE_PROVIDER": "r2",
            "R2_ACCOUNT_ID": "",
            "R2_ACCESS_KEY_ID": "",
            "R2_SECRET_ACCESS_KEY": "test-secret-value-do-not-log",
            "R2_BUCKET_NAME": "",
            "R2_ENDPOINT": "",
        }, clear=False):
            with self.assertRaises(StorageConfigurationError) as error:
                get_storage()
        self.assertIn("R2_ENDPOINT", str(error.exception))
        self.assertNotIn("test-secret-value-do-not-log", str(error.exception))

    def test_backblaze_endpoint_derives_region_without_account_id(self):
        client = FakeR2Client()
        captured = {}

        def make_client(*args, **kwargs):
            captured.update(kwargs)
            return client

        class FakeConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        boto3_module = types.ModuleType("boto3")
        boto3_module.client = make_client
        botocore_module = types.ModuleType("botocore")
        botocore_module.__path__ = []
        config_module = types.ModuleType("botocore.config")
        config_module.Config = FakeConfig

        with patch.dict(sys.modules, {
            "boto3": boto3_module,
            "botocore": botocore_module,
            "botocore.config": config_module,
        }), patch.dict(os.environ, {
            "R2_ACCOUNT_ID": "",
            "R2_ACCESS_KEY_ID": "test-key-id",
            "R2_SECRET_ACCESS_KEY": "test-secret-value",
            "R2_BUCKET_NAME": "test-bucket",
            "R2_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
        }, clear=False):
            storage = R2Storage.from_environment()

        self.assertIs(storage.client, client)
        self.assertEqual(storage.bucket, "test-bucket")
        self.assertEqual(captured["endpoint_url"], "https://s3.us-west-004.backblazeb2.com")
        self.assertEqual(captured["region_name"], "us-west-004")

    def test_locator_provider_is_stable_when_default_changes(self):
        clear_storage_cache()
        r2 = R2Storage(FakeR2Client(), "private-bucket")
        with patch.dict(os.environ, {"STORAGE_PROVIDER": "local"}, clear=False):
            self.assertIsInstance(get_storage(), LocalStorage)
            with patch.object(R2Storage, "from_environment", return_value=r2):
                self.assertIs(storage_for_locator("r2:documents/test/document.txt"), r2)

    def test_r2_processing_file_keeps_extension_and_cleans_up(self):
        storage = R2Storage(FakeR2Client(), "private-bucket")
        with storage.processing_path("r2:documents/test/document.docx", b"docx") as path:
            self.assertEqual(path.suffix, ".docx")
            self.assertEqual(path.read_bytes(), b"docx")
            temporary_path = path
        self.assertFalse(temporary_path.exists())


if __name__ == "__main__":
    unittest.main()
