
import json
import logging
import uuid
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.models.schemas import ChatRequest
from app.services import database
from app.routers.auth import get_current_user
from app.services.rag_service import (
    retrieve_relevant_chunks,
    create_rag_prompt,
    extract_sources,
)
from app.services.performance import measure_stage


logger = logging.getLogger(__name__)

router = APIRouter()

# Fixed response used when the selected documents do not provide
# enough relevant information to answer the question.
DOCUMENT_NOT_FOUND_MESSAGE = (
    "No relevant information was found in the selected "
    "document(s) to answer your question."
)

NO_DOCUMENT_MESSAGE = (
    "Please upload and select a document before asking questions."
)


# ---------------------------------------------------------------------
# LLM service
# ---------------------------------------------------------------------

def _get_llm_service():
    """Import the configured LLM service only when needed."""
    from app.services.llm_service import generate_response

    return generate_response


# ---------------------------------------------------------------------
# Conversation preparation
# ---------------------------------------------------------------------

def _prepare_chat(request: ChatRequest, user: dict) -> Tuple[str, List[str]]:
    """
    Validate the request, conversation, and selected documents.

    Strict document-only mode:
    At least one processed document must be explicitly selected.
    """
    message = (request.message or "").strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty.",
        )

    document_ids = list(
        dict.fromkeys(request.document_ids or [])
    )

    if not document_ids:
        raise HTTPException(
            status_code=400,
            detail=NO_DOCUMENT_MESSAGE,
        )

    conversation_id = request.conversation_id
    if conversation_id:
        conversation = database.get_conversation(conversation_id)

        if not conversation or conversation.get("owner_id") != user["id"]:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            )
    # Validate every selected document.
    documents = database.get_documents_for_owner(document_ids, user["id"])
    documents_by_id = {document["id"]: document for document in documents}
    for document_id in document_ids:
        document = documents_by_id.get(document_id)
        if not document:
            raise HTTPException(
                status_code=404,
                detail=f"Document not found: {document_id}",
            )

        if document["status"] != "processed":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Document is not ready: "
                    f"{document['filename']}"
                ),
            )


    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        title = message[:60].strip() or "New Chat"
        database.create_conversation(
            conversation_id,
            title,
            owner_id=user["id"],
        )

    # A user's library document can be used in more than one conversation.
    # Keep that association separately from the document record so selecting
    # an existing document never moves or duplicates it.
    attached_ids = database.attach_documents_to_conversation(
        document_ids, conversation_id, user["id"]
    )
    if len(attached_ids) != len(document_ids):
        raise HTTPException(status_code=404, detail="One or more documents were not found.")

    # Save the user's message.
    database.add_message(
        message_id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        role="user",
        content=message,
    )

    return conversation_id, document_ids


# ---------------------------------------------------------------------
# RAG prompt preparation
# ---------------------------------------------------------------------

async def _build_prompt(
    message: str,
    conversation_id: str,
    document_ids: List[str],
):
    """
    Retrieve information exclusively from selected documents.

    Returns:
        prompt
        history
        sources
    """
    def retrieve_from_selected_documents():
        chunks = database.get_chunks_for_documents(document_ids)
        return retrieve_relevant_chunks(
            query=message,
            chunks=chunks,
            top_k=5,
            document_ids=document_ids,
        )

    with measure_stage("rag_retrieval"):
        retrieved_chunks = await run_in_threadpool(retrieve_from_selected_documents)

    context_prompt = create_rag_prompt(
        question=message,
        retrieved_chunks=retrieved_chunks,
    )

    # Keep history in the database, but do not pass previous
    # answers to Gemini. This prevents unsupported old answers
    # from influencing document-only responses.
    history = []

    sources = extract_sources(retrieved_chunks)

    logger.info(
        "Document-only retrieval | conversation=%s | "
        "selected_documents=%s | retrieved_chunks=%s | sources=%s",
        conversation_id,
        len(document_ids),
        len(retrieved_chunks),
        len(sources),
    )

    return context_prompt, history, sources


# ---------------------------------------------------------------------
# Save fixed document-only response
# ---------------------------------------------------------------------

async def _save_document_not_found_response(
    conversation_id: str,
    sources: List[Dict],
) -> Dict:
    """
    Save and return a fixed response without calling Gemini.
    """
    await run_in_threadpool(
        database.add_message,
        message_id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        role="assistant",
        content=DOCUMENT_NOT_FOUND_MESSAGE,
        sources=sources,
    )

    return {
        "success": True,
        "conversation_id": conversation_id,
        "message": DOCUMENT_NOT_FOUND_MESSAGE,
        "sources": sources,
    }


# ---------------------------------------------------------------------
# SSE event helper
# ---------------------------------------------------------------------

def _sse_event(event_type: str, **data) -> str:
    """Create a consistently formatted SSE event."""
    payload = {
        "type": event_type,
        **data,
    }

    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------
# Main chat endpoint
# ---------------------------------------------------------------------

@router.post("/")
async def chat(request: ChatRequest, user: dict = Depends(get_current_user)):
    """
    Document-only chat endpoint.

    Gemini is called only when relevant document chunks
    have been retrieved.
    """
    try:
        conversation_id, document_ids = await run_in_threadpool(
            _prepare_chat, request, user
        )

        prompt, history, sources = await _build_prompt(
            request.message,
            conversation_id,
            document_ids,
        )

        # ---------------------------------------------------------
        # STRICT DOCUMENT-ONLY ENFORCEMENT
        # ---------------------------------------------------------
        # No retrieved sources means no Gemini request.
        if not sources:
            logger.info(
                "No relevant document information found. "
                "Skipping LLM request. conversation=%s",
                conversation_id,
            )

            if request.stream:
                return StreamingResponse(
                    _stream_document_not_found(
                        conversation_id,
                        sources,
                    ),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Conversation-Id": conversation_id,
                        "X-Accel-Buffering": "no",
                    },
                )

            return await _save_document_not_found_response(
                conversation_id,
                sources,
            )

        # Only load Gemini/OpenAI service when document context exists.
        generate_response = _get_llm_service()

        # ---------------------------------------------------------
        # STREAMING RESPONSE
        # ---------------------------------------------------------
        if request.stream:
            return StreamingResponse(
                _stream_chat(
                    generate_response=generate_response,
                    prompt=prompt,
                    history=history,
                    conversation_id=conversation_id,
                    sources=sources,
                    provider=request.provider,
                    model=request.model,
                    temperature=request.temperature,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Conversation-Id": conversation_id,
                    "X-Accel-Buffering": "no",
                },
            )

        # ---------------------------------------------------------
        # NON-STREAMING RESPONSE
        # ---------------------------------------------------------
        result = await generate_response(
            prompt=prompt,
            history=history,
            provider=request.provider,
            model=request.model,
            temperature=request.temperature,
        )

        answer = (
            result.get("content", "")
            if isinstance(result, dict)
            else str(result)
        )

        answer = answer.strip()

        if not answer:
            raise HTTPException(
                status_code=502,
                detail="AI provider returned an empty response.",
            )

        await run_in_threadpool(
            database.add_message,
            message_id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role="assistant",
            content=answer,
            sources=sources,
        )

        return {
            "success": True,
            "conversation_id": conversation_id,
            "message": answer,
            "sources": sources,
        }

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("Chat request failed")

        raise HTTPException(
            status_code=500,
            detail="Unable to process chat request.",
        ) from exc


# ---------------------------------------------------------------------
# Streaming: no relevant document information
# ---------------------------------------------------------------------

async def _stream_document_not_found(
    conversation_id: str,
    sources: List[Dict],
) -> AsyncGenerator[str, None]:
    """
    Stream the fixed not-found response.

    Gemini is never called in this function.
    """
    try:
        await run_in_threadpool(
            database.add_message,
            message_id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role="assistant",
            content=DOCUMENT_NOT_FOUND_MESSAGE,
            sources=sources,
        )

        yield _sse_event(
            "token",
            content=DOCUMENT_NOT_FOUND_MESSAGE,
        )

        yield _sse_event(
            "sources",
            sources=sources,
        )

        yield _sse_event(
            "done",
            conversation_id=conversation_id,
        )

    except Exception:
        logger.exception(
            "Failed to save document-not-found response."
        )

        yield _sse_event(
            "error",
            message="Unable to save the chat response.",
        )


# ---------------------------------------------------------------------
# Streaming: Gemini response
# ---------------------------------------------------------------------

async def _stream_chat(
    generate_response,
    prompt: str,
    history: List[Dict],
    conversation_id: str,
    sources: List[Dict],
    provider: Optional[str],
    model: Optional[str],
    temperature: float,
) -> AsyncGenerator[str, None]:
    """
    Stream an LLM response when relevant document context exists.

    Existing SSE event names are preserved:
    token, sources, done, error.
    """
    response_parts = []

    try:
        result = await generate_response(
            prompt=prompt,
            history=history,
            provider=provider,
            model=model,
            temperature=temperature,
            stream=True,
        )

        # ---------------------------------------------------------
        # ASYNC STREAM
        # ---------------------------------------------------------
        if hasattr(result, "__aiter__"):
            async for token in result:
                if isinstance(token, dict):
                    token = token.get("content", "")

                token = str(token)

                if not token:
                    continue

                response_parts.append(token)

                yield _sse_event(
                    "token",
                    content=token,
                )

        # ---------------------------------------------------------
        # NORMAL RESPONSE FALLBACK
        # ---------------------------------------------------------
        else:
            answer = (
                result.get("content", "")
                if isinstance(result, dict)
                else str(result)
            )

            answer = answer.strip()
            response_parts = [answer]

            if answer:
                yield _sse_event(
                    "token",
                    content=answer,
                )

        full_response = "".join(response_parts)
        if not full_response.strip():
            raise RuntimeError(
                "AI provider returned an empty response."
            )

        # Save the complete assistant response.
        await run_in_threadpool(
            database.add_message,
            message_id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role="assistant",
            content=full_response,
            sources=sources,
        )

        yield _sse_event(
            "sources",
            sources=sources,
        )

        yield _sse_event(
            "done",
            conversation_id=conversation_id,
        )

    except Exception:
        logger.exception("Streaming chat failed")

        yield _sse_event(
            "error",
            message="AI response generation failed.",
        )
