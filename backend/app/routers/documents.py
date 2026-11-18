
import asyncio
import logging
import hashlib
import uuid
from pathlib import Path
from urllib.parse import quote
import mimetypes

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.services import database
from app.services.serialization import public_document
from app.routers.auth import get_current_user
from app.services.document_processor import (
    DocumentProcessingError,
    MAX_FILE_SIZE,
    process_document,
    validate_file,
)
from app.services.rag_service import generate_embeddings
from app.services.performance import measure_stage
from app.services.storage import (
    StorageError,
    build_document_key,
    get_storage,
    storage_for_locator,
)


logger = logging.getLogger(__name__)

router = APIRouter()


def _is_retryable_cleanup_error(error: Exception) -> bool:
    """Turso can expire a request stream; a new statement can safely retry."""
    message = str(error).casefold()
    return "stream not found" in message and "404" in message


async def _delete_failed_upload_record(document_id: str, owner_id: str) -> bool:
    """Retry the idempotent delete after Turso's expired-stream 404 response."""
    for attempt in range(3):
        try:
            await run_in_threadpool(database.delete_document, document_id, owner_id)
            return True
        except Exception as exc:
            if not _is_retryable_cleanup_error(exc) or attempt == 2:
                logger.exception(
                    "Failed to remove incomplete document record %s; it remains for cleanup retry.",
                    document_id,
                )
                return False
            delay = 0.2 * (2 ** attempt)
            logger.warning(
                "Turso cleanup stream expired for incomplete document %s; retrying in %.1fs.",
                document_id,
                delay,
            )
            await asyncio.sleep(delay)
    return False


async def _delete_failed_upload_object(storage_locator: str) -> bool:
    """Retry idempotent object deletion before removing its recovery record."""
    for attempt in range(3):
        try:
            storage = storage_for_locator(storage_locator)
            await run_in_threadpool(storage.delete, storage_locator)
            return True
        except Exception:
            if attempt == 2:
                logger.exception(
                    "Failed to clean up incomplete upload object %s; keeping its database record for retry.",
                    storage_locator,
                )
                return False
            delay = 0.2 * (2 ** attempt)
            logger.warning(
                "Incomplete upload object cleanup failed; retrying in %.1fs.",
                delay,
                exc_info=True,
            )
            await asyncio.sleep(delay)
    return False


async def cleanup_failed_upload(
    document_id: str,
    storage_locator: str | None,
    owner_id: str,
) -> None:
    """Remove incomplete document records and uploaded files."""

    if storage_locator and not await _delete_failed_upload_object(storage_locator):
        # Keep the processing row as a recovery reference if the private
        # object could not be removed.
        return

    await _delete_failed_upload_record(document_id, owner_id)


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
)
async def upload_documents(
    files: list[UploadFile] = File(...),
    conversation_id: str | None = Form(default=None),
    user: dict = Depends(get_current_user),
):
    if not files:
        raise HTTPException(
            status_code=400,
            detail="Please select at least one document.",
        )

    if len(files) > 10:
        raise HTTPException(
            status_code=400,
            detail="Maximum 10 files allowed per upload.",
        )

    if conversation_id:
        conversation = await run_in_threadpool(database.get_conversation, conversation_id)

        if not conversation or conversation.get("owner_id") != user["id"]:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            )

    uploaded_documents = []
    errors = []

    for file in files:
        document_id = str(uuid.uuid4())

        safe_name = Path(file.filename or "").name

        storage_locator = None

        if not safe_name:
            errors.append({
                "filename": "Unknown",
                "error": "Invalid filename.",
            })
            await file.close()
            continue

        extension = Path(safe_name).suffix.lower()

        try:
            # Read and validate uploaded file.
            content = await file.read(MAX_FILE_SIZE + 1)
            file_size = len(content)

            valid, message = validate_file(
                safe_name,
                file_size,
            )

            if not valid:
                errors.append({
                    "filename": safe_name,
                    "error": message,
                })
                continue

            # Reuse an identical document already in this user's library.
            # Hashes are scoped by owner, so another account's uploads are
            # never disclosed or reused.
            content_hash = await run_in_threadpool(
                lambda: hashlib.sha256(content).hexdigest()
            )
            existing = await run_in_threadpool(
                database.find_document_by_hash,
                user["id"], content_hash, file_size,
            )
            if existing:
                if existing.get("status") != "processed":
                    errors.append({
                        "filename": safe_name,
                        "error": "An identical document is already being processed or is unavailable.",
                    })
                    continue

                if conversation_id:
                    await run_in_threadpool(
                        database.attach_document_to_conversation,
                        existing["id"],
                        conversation_id,
                        user["id"],
                    )
                uploaded_documents.append(public_document(existing))
                continue

            # Object keys are generated by the backend and never use the
            # client-supplied filename as a path component.
            storage = get_storage()
            object_key = build_document_key(document_id, extension)
            storage_locator = storage.locator(object_key)
            await run_in_threadpool(
                storage.put,
                object_key,
                content,
                mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            )
            if storage.provider == "local":
                storage_locator = storage._path_for_key(object_key)
                storage_locator = str(storage_locator)

            # Create document database record.
            await run_in_threadpool(
                database.create_document,
                document_id=document_id,
                conversation_id=conversation_id,
                filename=safe_name,
                file_type=extension.lstrip("."),
                file_size=file_size,
                # Keep the existing column and all legacy records intact. Local
                # rows retain a filesystem path; R2 rows store only an internal
                # provider marker and object key, never a public URL.
                file_path=storage_locator,
                owner_id=user["id"],
                content_hash=content_hash,
            )

            try:
                # Extract text and create chunks.
                with measure_stage("document_parsing"):
                    with storage.processing_path(storage_locator, content) as processing_path:
                        processed = await run_in_threadpool(process_document, processing_path)

                chunks = processed["chunks"]

                if not chunks:
                    raise DocumentProcessingError(
                        "Document contains no searchable chunks."
                    )

                # Generate embeddings for all chunks together.
                chunk_texts = [
                    chunk["content"]
                    for chunk in chunks
                ]

                with measure_stage("embedding"):
                    embeddings = await run_in_threadpool(
                        generate_embeddings,
                        chunk_texts,
                    )

                if len(embeddings) != len(chunks):
                    raise RuntimeError(
                        "Embedding count does not match chunk count."
                    )

                # Persist the vectors in bounded, multi-row database writes.
                await run_in_threadpool(
                    database.add_chunks,
                    document_id,
                    chunks,
                    [embedding.tobytes() for embedding in embeddings],
                )

                # Mark document as processed.
                await run_in_threadpool(
                    database.update_document_status,
                    document_id=document_id,
                    status="processed",
                    total_pages=processed["total_pages"],
                    total_chunks=processed["total_chunks"],
                )

                uploaded_documents.append({
                    "id": document_id,
                    "filename": safe_name,
                    "file_type": extension.lstrip("."),
                    "file_size": file_size,
                    "status": "processed",
                    "total_pages": processed["total_pages"],
                    "total_chunks": processed["total_chunks"],
                })

                logger.info(
                    "Document processed successfully: %s | chunks=%s",
                    safe_name,
                    len(chunks),
                )

            except DocumentProcessingError as exc:
                logger.exception(
                    "Document processing failed: %s",
                    safe_name,
                )

                await cleanup_failed_upload(
                    document_id,
                    storage_locator,
                    user["id"],
                )

                errors.append({
                    "filename": safe_name,
                    "error": str(exc),
                })

            except Exception:
                logger.exception(
                    "Document embedding or storage failed: %s",
                    safe_name,
                )

                await cleanup_failed_upload(
                    document_id,
                    storage_locator,
                    user["id"],
                )

                errors.append({
                    "filename": safe_name,
                    "error": "Document processing failed.",
                })

        except Exception:
            logger.exception(
                "Document upload failed: %s",
                safe_name,
            )

            await cleanup_failed_upload(
                document_id,
                storage_locator,
                user["id"],
            )

            errors.append({
                "filename": safe_name,
                "error": "Document upload failed.",
            })

        finally:
            await file.close()

    return {
        "success": len(uploaded_documents) > 0,
        "message": (
            "Documents uploaded successfully."
            if uploaded_documents
            else "No documents could be uploaded."
        ),
        "documents": uploaded_documents,
        "errors": errors,
        "total_uploaded": len(uploaded_documents),
        "total_failed": len(errors),
    }


@router.get("/")
async def list_documents(
    conversation_id: str | None = None,
    user: dict = Depends(get_current_user),
):
    if conversation_id:
        exists, documents = await run_in_threadpool(
            database.get_conversation_documents_for_owner,
            conversation_id,
            user["id"],
        )
        if not exists:
            raise HTTPException(status_code=404, detail="Conversation not found.")
    else:
        documents = await run_in_threadpool(database.list_documents, None, user["id"])

    return {
        "success": True,
        "documents": [public_document(document) for document in documents],
        "total": len(documents),
    }


@router.get("/{document_id}")
async def get_document_details(
    document_id: str,
    user: dict = Depends(get_current_user),
):
    document = await run_in_threadpool(database.get_document_for_owner, document_id, user["id"])

    if not document:
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    return {
        "success": True,
        "document": public_document(document),
    }


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    user: dict = Depends(get_current_user),
):
    """Download a private document after checking its database ownership."""
    document = await run_in_threadpool(
        database.get_document_for_owner, document_id, user["id"]
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")

    locator = str(document.get("file_path") or "")
    try:
        storage = storage_for_locator(locator)
        content = await run_in_threadpool(storage.read, locator)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Stored document not found.") from None
    except StorageError:
        logger.warning("Document download failed for document %s (storage unavailable).", document_id)
        raise HTTPException(status_code=503, detail="Document storage is temporarily unavailable.") from None

    filename = Path(str(document.get("filename") or "document")).name
    safe_ascii_name = "".join(
        character if character.isascii() and character not in {'"', "\\", ";", "\r", "\n"} else "_"
        for character in filename
    ) or "document"
    content_disposition = (
        f'attachment; filename="{safe_ascii_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition},
    )


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    user: dict = Depends(get_current_user),
):
    document = await run_in_threadpool(database.get_document_for_owner, document_id, user["id"])

    if not document:
        raise HTTPException(
            status_code=404,
            detail="Document not found.",
        )

    # Ownership is checked before touching the configured file store. Remove
    # the private object first; a retry can safely finish the DB delete if that
    # transaction fails because provider deletion is idempotent.
    locator = str(document.get("file_path") or "")
    try:
        storage = storage_for_locator(locator)
        await run_in_threadpool(storage.delete, locator)
    except StorageError:
        logger.warning("Document deletion failed for document %s (storage unavailable).", document_id)
        raise HTTPException(status_code=503, detail="Document storage is temporarily unavailable.") from None

    await run_in_threadpool(database.delete_document, document_id, user["id"])

    return {
        "success": True,
        "message": "Document deleted successfully.",
        "document_id": document_id,
    }
