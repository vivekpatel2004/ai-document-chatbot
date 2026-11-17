"""Private document storage for local development and S3-compatible R2/B2.

Document locators are persisted in the existing ``documents.file_path`` column.
New local records keep their existing absolute-path format. R2 records use an
internal ``r2:<object-key>`` locator; this is not a URL and is never returned
from public API serializers. Legacy absolute local paths remain readable.
"""

from __future__ import annotations

import mimetypes
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit


BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LOCAL_STORAGE_DIR = BACKEND_DIR.parent / "uploads"
R2_LOCATOR_PREFIX = "r2:"


class StorageError(RuntimeError):
    """Safe, user-facing storage failure without provider secrets."""


class StorageConfigurationError(StorageError):
    """Storage provider configuration is incomplete or invalid."""


def _signing_region(endpoint: str) -> str:
    """Use B2's endpoint region while preserving R2's required ``auto``."""
    host = (urlsplit(endpoint).hostname or "").lower()
    match = re.fullmatch(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", host)
    if match:
        return match.group(1)
    if host.endswith(".backblazeb2.com"):
        raise StorageConfigurationError(
            "R2_ENDPOINT must use the Backblaze regional S3 endpoint."
        )
    return "auto"


def _validated_object_key(key: str) -> str:
    if not isinstance(key, str) or not key or "\x00" in key or "\\" in key:
        raise StorageError("Invalid document object key.")
    if key.startswith("/") or re.match(r"^[A-Za-z]:", key):
        raise StorageError("Invalid document object key.")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise StorageError("Invalid document object key.")
    return key


def build_document_key(document_id: str, extension: str) -> str:
    """Build a server-generated key; client filenames never become key paths."""
    safe_id = str(document_id)
    if not re.fullmatch(r"[A-Fa-f0-9-]{16,64}", safe_id):
        raise StorageError("Invalid document identifier.")
    safe_extension = extension.lower().lstrip(".")
    if safe_extension not in {"pdf", "docx", "txt"}:
        raise StorageError("Invalid document type.")
    return f"documents/{safe_id}/document.{safe_extension}"


class LocalStorage:
    provider = "local"

    def __init__(self, root: Path | str = DEFAULT_LOCAL_STORAGE_DIR) -> None:
        self.root = Path(root).resolve()
        self.legacy_roots = {
            self.root,
            (BACKEND_DIR / "uploads").resolve(),
        }

    def _path_for_key(self, key: str) -> Path:
        key = _validated_object_key(key)
        path = (self.root / Path(*key.split("/"))).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError("Invalid document object key.")
        return path

    def locator(self, key: str) -> str:
        return str(self._path_for_key(key))

    def _path_for_locator(self, locator: str) -> Path:
        if locator.startswith(R2_LOCATOR_PREFIX):
            raise StorageError("Document belongs to another storage provider.")

        raw_path = Path(locator)
        if not raw_path.is_absolute():
            # Relative paths were never emitted by the application. Accept only
            # a valid key beneath the configured local root for safe recovery.
            return self._path_for_key(locator)

        path = raw_path.resolve()
        if not any(path.is_relative_to(root) for root in self.legacy_roots):
            raise StorageError("Stored document path is outside local storage.")
        return path

    def put(self, key: str, content: bytes, content_type: str | None = None) -> str:
        del content_type  # Local files do not need content-type metadata.
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as destination:
                destination.write(content)
        except FileExistsError:
            raise StorageError("A document already exists at this storage key.") from None
        except OSError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageError("Unable to store the uploaded document locally.") from None
        return str(path)

    def read(self, locator: str) -> bytes:
        path = self._path_for_locator(locator)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise
        except OSError:
            raise StorageError("Unable to read the stored document.") from None

    def delete(self, locator: str) -> None:
        path = self._path_for_locator(locator)
        try:
            path.unlink(missing_ok=True)
            if path.parent != self.root:
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
        except OSError:
            raise StorageError("Unable to delete the stored document.") from None

    @contextmanager
    def processing_path(self, locator: str, upload_bytes: bytes) -> Iterator[Path]:
        del upload_bytes
        yield self._path_for_locator(locator)


class R2Storage:
    """Existing S3 adapter for Cloudflare R2 and compatible endpoints such as B2."""

    provider = "r2"

    def __init__(self, client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    @classmethod
    def from_environment(cls) -> "R2Storage":
        account_id = os.getenv("R2_ACCOUNT_ID", "").strip()
        access_key = os.getenv("R2_ACCESS_KEY_ID", "").strip()
        secret_key = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
        bucket = os.getenv("R2_BUCKET_NAME", "").strip()
        endpoint = os.getenv("R2_ENDPOINT", "").strip()

        missing = [
            name for name, value in (
                ("R2_ACCESS_KEY_ID", access_key),
                ("R2_SECRET_ACCESS_KEY", secret_key),
                ("R2_BUCKET_NAME", bucket),
            ) if not value
        ]
        if not endpoint and not account_id:
            missing.append("R2_ENDPOINT (or R2_ACCOUNT_ID for Cloudflare R2)")
        if missing:
            raise StorageConfigurationError(
                "Missing R2 storage configuration: " + ", ".join(missing)
            )
        if not endpoint:
            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        parsed_endpoint = urlsplit(endpoint)
        if parsed_endpoint.scheme != "https" or not parsed_endpoint.hostname:
            raise StorageConfigurationError("R2_ENDPOINT must be a valid HTTPS endpoint.")
        signing_region = _signing_region(endpoint)

        try:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=signing_region,
                config=Config(signature_version="s3v4"),
            )
        except StorageError:
            raise
        except Exception:
            raise StorageConfigurationError(
                "Unable to initialize Cloudflare R2 storage."
            ) from None
        return cls(client, bucket)

    def locator(self, key: str) -> str:
        return R2_LOCATOR_PREFIX + _validated_object_key(key)

    def _key_from_locator(self, locator: str) -> str:
        if not locator.startswith(R2_LOCATOR_PREFIX):
            raise StorageError("Document belongs to another storage provider.")
        return _validated_object_key(locator[len(R2_LOCATOR_PREFIX):])

    def put(self, key: str, content: bytes, content_type: str | None = None) -> str:
        safe_key = _validated_object_key(key)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=safe_key,
                Body=content,
                ContentType=content_type or mimetypes.guess_type(safe_key)[0] or "application/octet-stream",
            )
        except Exception:
            # Do not propagate SDK exceptions that could contain request or
            # credential details into application logs or API responses.
            raise StorageError("Unable to store the uploaded document in R2.") from None
        return self.locator(safe_key)

    def read(self, locator: str) -> bytes:
        key = self._key_from_locator(locator)
        body = None
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"]
            return body.read()
        except Exception:
            raise StorageError("Unable to read the stored document from R2.") from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    pass

    def delete(self, locator: str) -> None:
        key = self._key_from_locator(locator)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            raise StorageError("Unable to delete the stored document from R2.") from None

    @contextmanager
    def processing_path(self, locator: str, upload_bytes: bytes) -> Iterator[Path]:
        suffix = Path(self._key_from_locator(locator)).suffix
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="ai-document-chat-", suffix=suffix, delete=False
            ) as temporary:
                path = Path(temporary.name)
                temporary.write(upload_bytes)
            yield path
        finally:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


_storage_cache: dict[str, LocalStorage | R2Storage] = {}
_storage_lock = threading.Lock()


def get_storage() -> LocalStorage | R2Storage:
    provider = os.getenv("STORAGE_PROVIDER", "local").strip().lower() or "local"
    if provider not in {"local", "r2"}:
        raise StorageConfigurationError(
            "STORAGE_PROVIDER must be either 'local' or 'r2'."
        )
    with _storage_lock:
        storage = _storage_cache.get(provider)
        if storage is None:
            storage = LocalStorage() if provider == "local" else R2Storage.from_environment()
            _storage_cache[provider] = storage
        return storage


def storage_for_locator(locator: str) -> LocalStorage | R2Storage:
    """Resolve a persisted locator independently of the current provider.

    This allows existing local files to remain accessible after setting R2 for
    new uploads; no existing object is moved or rewritten.
    """
    if locator.startswith(R2_LOCATOR_PREFIX):
        with _storage_lock:
            storage = _storage_cache.get("r2")
            if storage is None:
                storage = R2Storage.from_environment()
                _storage_cache["r2"] = storage
            return storage
    with _storage_lock:
        storage = _storage_cache.get("local")
        if storage is None:
            storage = LocalStorage()
            _storage_cache["local"] = storage
        return storage


def clear_storage_cache() -> None:
    """Clear provider clients for tests and controlled runtime reconfiguration."""
    with _storage_lock:
        _storage_cache.clear()
