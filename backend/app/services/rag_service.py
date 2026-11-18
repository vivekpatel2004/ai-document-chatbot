
import logging
import re
import threading
import hashlib
import os
import time
from collections import deque
from email.utils import parsedate_to_datetime
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import numpy as np
from app.services.performance import measure_stage


logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSION = 768
EMBEDDING_BATCH_SIZE = 64  # Gemini batch requests support up to 100 inputs.
EMBEDDING_TIMEOUT_SECONDS = 30.0
EMBEDDING_MAX_RETRIES = 4
EMBEDDING_REQUESTS_PER_MINUTE = 60
DEFAULT_TOP_K = 5
DEFAULT_SCORE_THRESHOLD = 0.05

_embedding_request_gate = threading.BoundedSemaphore(2)
_embedding_rate_lock = threading.Lock()
_embedding_rate_events: deque[tuple[float, int]] = deque()
_fallback_embedding_lock = threading.RLock()
_fallback_embedding_cache: OrderedDict[tuple[str, str, str], np.ndarray] = OrderedDict()
_FALLBACK_EMBEDDING_CACHE_SIZE = 2048


# ---------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------

def _get_embedding_api_key() -> str:
    # Match the existing chat service's precedence so both features select the
    # same key when both aliases happen to be configured.
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing Gemini API key. Set GEMINI_API_KEY or GOOGLE_API_KEY."
        )
    return api_key


def _retry_delay(error: Exception | None, attempt: int) -> float:
    if error is not None:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None) or getattr(error, "headers", {})
        retry_after = headers.get("Retry-After") if headers else None
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 30.0)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return min(max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0), 30.0)
                except (TypeError, ValueError, OverflowError):
                    pass
    return min(0.5 * (2 ** attempt), 8.0)


def _wait_for_rate_capacity(request_count: int) -> None:
    """Pace individual embeddings, since a batch contains multiple requests."""
    limit = int(os.getenv(
        "GEMINI_EMBEDDING_REQUESTS_PER_MINUTE",
        EMBEDDING_REQUESTS_PER_MINUTE,
    ))
    if limit <= 0 or request_count > limit:
        raise ValueError(
            "GEMINI_EMBEDDING_REQUESTS_PER_MINUTE must be positive and "
            "at least as large as the current batch."
        )

    while True:
        wait_seconds = 0.0
        with _embedding_rate_lock:
            now = time.monotonic()
            while _embedding_rate_events and _embedding_rate_events[0][0] <= now - 60.0:
                _embedding_rate_events.popleft()
            used = sum(weight for _, weight in _embedding_rate_events)
            if used + request_count <= limit:
                _embedding_rate_events.append((now, request_count))
                return
            wait_seconds = max(
                0.01,
                _embedding_rate_events[0][0] + 60.0 - now,
            )
        time.sleep(wait_seconds)


def _request_embedding_batch(
    texts: Sequence[str], task_type: str, client: Any,
) -> list[list[float]]:
    from google.genai import types

    for attempt in range(EMBEDDING_MAX_RETRIES + 1):
        try:
            _wait_for_rate_capacity(len(texts))
            with _embedding_request_gate:
                response = client.models.embed_content(
                    model=DEFAULT_EMBEDDING_MODEL,
                    contents=list(texts),
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=EMBEDDING_DIMENSION,
                    ),
                )
            results = getattr(response, "embeddings", None)
            if results is None and isinstance(response, dict):
                results = response.get("embeddings")
            if not isinstance(results, list) or len(results) != len(texts):
                raise ValueError("Gemini returned an unexpected number of embeddings.")
            vectors = []
            for item in results:
                values = getattr(item, "values", None)
                if values is None and isinstance(item, dict):
                    values = item.get("values")
                if not isinstance(values, list) or len(values) != EMBEDDING_DIMENSION:
                    raise ValueError(f"Gemini embedding must have {EMBEDDING_DIMENSION} dimensions.")
                vectors.append(values)
            return vectors
        except Exception as exc:
            status_code = getattr(exc, "code", None)
            response = getattr(exc, "response", None)
            status_code = (
                status_code
                or getattr(exc, "status_code", None)
                or getattr(response, "status_code", None)
            )
            error_name = type(exc).__name__.casefold()
            retryable = (
                isinstance(exc, (httpx.RequestError, TimeoutError, ConnectionError, OSError))
                or "timeout" in error_name
                or "connection" in error_name
                or status_code in {
                408, 425, 429, 500, 502, 503, 504,
                }
            )
            if not retryable or attempt >= EMBEDDING_MAX_RETRIES:
                raise
            delay = _retry_delay(exc, attempt)
            logger.warning("Gemini embedding request failed (HTTP %s); retrying in %.1fs.", status_code or "network", delay)
            time.sleep(delay)
    raise RuntimeError("Gemini embedding request failed after retries.")


def generate_embeddings(
    texts: Sequence[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> np.ndarray:
    """
    Generate normalized float32 Gemini embeddings for a sequence of texts.
    """
    cleaned_texts = [
        str(text).strip()
        for text in texts
        if text is not None and str(text).strip()
    ]

    if not cleaned_texts:
        return np.empty((0, 0), dtype=np.float32)

    if task_type not in {"RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"}:
        raise ValueError("Unsupported Gemini retrieval embedding task type.")

    timeout = float(os.getenv("GEMINI_EMBEDDING_TIMEOUT_SECONDS", EMBEDDING_TIMEOUT_SECONDS))
    batch_size = int(os.getenv("GEMINI_EMBEDDING_BATCH_SIZE", EMBEDDING_BATCH_SIZE))
    requests_per_minute = int(os.getenv(
        "GEMINI_EMBEDDING_REQUESTS_PER_MINUTE",
        EMBEDDING_REQUESTS_PER_MINUTE,
    ))
    if timeout <= 0 or not 1 <= batch_size <= 100 or requests_per_minute <= 0:
        raise ValueError(
            "Gemini embedding timeout and requests-per-minute must be positive; "
            "batch size must be between 1 and 100."
        )
    batch_size = min(batch_size, requests_per_minute)

    vectors: list[list[float]] = []
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=_get_embedding_api_key(),
        http_options=types.HttpOptions(
            timeout=int(timeout * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    try:
        for start in range(0, len(cleaned_texts), batch_size):
            batch = cleaned_texts[start:start + batch_size]
            vectors.extend(_request_embedding_batch(batch, task_type, client))
    finally:
        client.close()

    embeddings = np.asarray(vectors, dtype=np.float32)

    if not np.all(np.isfinite(embeddings)):
        raise ValueError(
            "Embedding generation returned non-finite values."
        )

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    if np.any(norms <= 1e-12):
        raise ValueError("Embedding generation returned a zero-magnitude vector.")
    return embeddings / norms


def create_embedding(text: str) -> np.ndarray:
    """
    Generate one normalized embedding.
    """
    if not text or not text.strip():
        raise ValueError("Cannot create an embedding from empty text.")

    embeddings = generate_embeddings([text], task_type="RETRIEVAL_QUERY")

    if embeddings.shape[0] != 1:
        raise ValueError("Failed to generate the text embedding.")

    return embeddings[0]


# ---------------------------------------------------------------------
# Stored embedding conversion
# ---------------------------------------------------------------------

def _decode_stored_embedding(
    stored_embedding: Any,
) -> Optional[np.ndarray]:
    """
    Convert a stored Gemini embedding BLOB into a normalized float32 vector.

    Returns None if the stored value is missing or invalid.
    """
    if stored_embedding is None:
        return None

    try:
        if isinstance(stored_embedding, memoryview):
            stored_embedding = stored_embedding.tobytes()

        if isinstance(stored_embedding, bytes):
            if not stored_embedding:
                return None

            vector = np.frombuffer(
                stored_embedding,
                dtype=np.float32,
            ).copy()

        elif isinstance(stored_embedding, (bytearray,)):
            vector = np.frombuffer(
                bytes(stored_embedding),
                dtype=np.float32,
            ).copy()

        elif isinstance(stored_embedding, np.ndarray):
            vector = np.asarray(
                stored_embedding,
                dtype=np.float32,
            ).reshape(-1)

        else:
            return None

        if vector.ndim != 1 or vector.size == 0:
            return None

        if not np.all(np.isfinite(vector)):
            return None

        norm = float(np.linalg.norm(vector))

        if norm <= 1e-12:
            return None

        return vector / norm

    except (TypeError, ValueError, BufferError):
        logger.warning(
            "A stored chunk embedding could not be decoded."
        )
        return None


# ---------------------------------------------------------------------
# Similarity calculation
# ---------------------------------------------------------------------

def cosine_similarity(
    query_embedding: np.ndarray,
    document_embeddings: np.ndarray,
) -> np.ndarray:
    """
    Calculate cosine similarity between one query vector and
    multiple document vectors.

    Both inputs are normalized before the dot product.
    """
    query_vector = np.asarray(
        query_embedding,
        dtype=np.float32,
    ).reshape(-1)

    document_vectors = np.asarray(
        document_embeddings,
        dtype=np.float32,
    )

    if document_vectors.size == 0:
        return np.empty(0, dtype=np.float32)

    if document_vectors.ndim == 1:
        document_vectors = document_vectors.reshape(1, -1)

    if query_vector.size != document_vectors.shape[1]:
        raise ValueError(
            "Query and document embedding dimensions do not match."
        )

    query_norm = float(np.linalg.norm(query_vector))

    if query_norm <= 1e-12:
        raise ValueError("Query embedding has zero magnitude.")

    document_norms = np.linalg.norm(
        document_vectors,
        axis=1,
        keepdims=True,
    )

    safe_norms = np.where(
        document_norms <= 1e-12,
        1.0,
        document_norms,
    )

    normalized_query = query_vector / query_norm
    normalized_documents = document_vectors / safe_norms

    scores = np.dot(
        normalized_documents,
        normalized_query,
    )

    return np.asarray(scores, dtype=np.float32)


# ---------------------------------------------------------------------
# Chunk retrieval
# ---------------------------------------------------------------------

def retrieve_relevant_chunks(
    query: str,
    chunks: List[Dict[str, Any]],
    top_k: int = DEFAULT_TOP_K,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    document_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve the most relevant chunks for a user question.

    Embedding strategy:
    1. Generate the query embedding once.
    2. Reuse valid embeddings stored in SQLite.
    3. Generate embeddings only for chunks with missing or invalid
       stored embeddings.
    4. Calculate cosine similarity and return the best matching chunks.

    This function does not modify the database.
    """
    if not query or not query.strip():
        return []

    if not chunks:
        return []

    if top_k <= 0:
        return []

    # Only retrieve from explicitly selected documents when supplied.
    if document_ids is not None:
        selected_document_ids = set(document_ids)

        chunks = [
            chunk
            for chunk in chunks
            if chunk.get("document_id") in selected_document_ids
        ]

    # Ignore chunks without usable text.
    valid_chunks = [
        chunk
        for chunk in chunks
        if chunk.get("content")
        and str(chunk.get("content")).strip()
    ]

    if not valid_chunks:
        return []

    # Generate the question embedding once.
    with measure_stage("embedding"):
        query_embedding = create_embedding(query)

    query_dimension = query_embedding.shape[0]

    valid_embeddings: List[Optional[np.ndarray]] = []
    missing_indices: List[int] = []
    missing_keys: List[tuple[str, str, str]] = []
    missing_texts: List[str] = []

    # First, reuse vectors already stored in SQLite.
    for index, chunk in enumerate(valid_chunks):
        stored_vector = None
        if chunk.get("embedding_model") == DEFAULT_EMBEDDING_MODEL:
            stored_vector = _decode_stored_embedding(chunk.get("embedding"))

        # A vector with a different dimension cannot be compared.
        if (
            stored_vector is not None
            and stored_vector.shape[0] != query_dimension
        ):
            stored_vector = None

        valid_embeddings.append(stored_vector)

        if stored_vector is None:
            missing_indices.append(index)
            text = str(chunk["content"]).strip()
            chunk_key = str(
                chunk.get("id")
                or f"{chunk.get('document_id', '')}:{chunk.get('chunk_index', index)}"
            )
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            key = (chunk_key, digest, DEFAULT_EMBEDDING_MODEL)
            missing_keys.append(key)

    to_embed_keys = []
    to_embed_texts = []
    to_embed_indices = []
    with _fallback_embedding_lock:
        for position, key in enumerate(missing_keys):
            cached = _fallback_embedding_cache.get(key)
            if cached is not None and cached.shape[0] == query_dimension:
                _fallback_embedding_cache.move_to_end(key)
                valid_embeddings[missing_indices[position]] = cached.copy()
            else:
                text = str(valid_chunks[missing_indices[position]]["content"]).strip()
                to_embed_keys.append(key)
                to_embed_texts.append(text)
                to_embed_indices.append(missing_indices[position])

    # Generate vectors only for chunks that need a fallback.
    if to_embed_texts:
        logger.info(
            "Generating fallback embeddings for %s chunks "
            "without usable stored embeddings.",
            len(to_embed_texts),
        )

        with measure_stage("embedding"):
            fallback_embeddings = generate_embeddings(to_embed_texts)

        if fallback_embeddings.shape[0] != len(to_embed_keys):
            raise ValueError(
                "Fallback embedding count does not match "
                "the number of missing chunk embeddings."
            )

        if fallback_embeddings.shape[1] != query_dimension:
            raise ValueError(
                "Fallback embedding dimension does not match "
                "the query embedding dimension."
            )

        with _fallback_embedding_lock:
            for position, key in enumerate(to_embed_keys):
                vector = fallback_embeddings[position].copy()
                _fallback_embedding_cache[key] = vector
                _fallback_embedding_cache.move_to_end(key)
                valid_embeddings[to_embed_indices[position]] = vector.copy()
            while len(_fallback_embedding_cache) > _FALLBACK_EMBEDDING_CACHE_SIZE:
                _fallback_embedding_cache.popitem(last=False)

    # At this point every valid chunk should have an embedding.
    complete_embeddings = [
        embedding
        for embedding in valid_embeddings
        if embedding is not None
    ]

    if len(complete_embeddings) != len(valid_chunks):
        raise ValueError(
            "Some document chunks could not be embedded."
        )

    document_vectors = np.vstack(
        complete_embeddings
    ).astype(np.float32, copy=False)

    # Calculate similarity scores.
    scores = cosine_similarity(
        query_embedding,
        document_vectors,
    )

    # Rank chunks by similarity.
    ranked_indices = np.argsort(scores)[::-1]
    retrieved_chunks: List[Dict[str, Any]] = []
    seen_document_text = set()

    for index in ranked_indices:
        score = float(scores[index])

        if score < score_threshold:
            continue

        result = dict(valid_chunks[int(index)])
        normalized_text = re.sub(
            r"\s+", " ", str(result.get("content", "")).strip()
        ).casefold()
        duplicate_key = (result.get("document_id"), normalized_text)
        if not normalized_text or duplicate_key in seen_document_text:
            continue
        seen_document_text.add(duplicate_key)

        # Do not expose large binary embedding data in the response.
        result.pop("embedding", None)

        result["relevance_score"] = round(score, 4)

        retrieved_chunks.append(result)

        if len(retrieved_chunks) >= top_k:
            break

    logger.info(
        "Retrieved %s unique relevant chunks from %s available chunks.",
        len(retrieved_chunks),
        len(valid_chunks),
    )

    return retrieved_chunks


# ---------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------

def build_context(
    retrieved_chunks: List[Dict[str, Any]],
) -> str:
    """
    Build readable context from retrieved document chunks.
    """
    if not retrieved_chunks:
        return ""

    context_parts = []

    source_numbers = {}

    for index, chunk in enumerate(retrieved_chunks, start=1):
        filename = chunk.get("filename") or "Unknown document"
        document_id = chunk.get("document_id") or filename
        page_number = chunk.get("page_number")
        content = str(chunk.get("content", "")).strip()

        if not content:
            continue

        source_key = (document_id, page_number)
        if source_key not in source_numbers:
            source_numbers[source_key] = len(source_numbers) + 1
        citation = source_numbers[source_key]
        source_details = [f"Source [{citation}]: {filename}"]

        if page_number is not None:
            source_details.append(f"Page: {page_number}")

        context_parts.append(
            f"[Document Context {index}]\n"
            f"{' | '.join(source_details)}\n"
            f"{content}"
        )

    return "\n\n".join(context_parts)


# ---------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------

def extract_sources(
    retrieved_chunks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Extract unique source information from retrieved chunks.

    Sources are deduplicated by document and page. The ranked result order
    keeps the strongest retrieved chunk for each citation.
    """
    sources: List[Dict[str, Any]] = []
    seen_sources = set()

    for chunk in retrieved_chunks:
        document_id = chunk.get("document_id")
        filename = chunk.get("filename") or "Unknown document"
        page_number = chunk.get("page_number")
        chunk_index = chunk.get("chunk_index")

        if not str(chunk.get("content", "")).strip():
            continue
        source_key = (document_id or filename, page_number)

        if source_key in seen_sources:
            continue

        seen_sources.add(source_key)

        source = {
            "document_id": document_id,
            "filename": filename,
            "page_number": page_number,
            "chunk_index": chunk_index,
        }

        if chunk.get("relevance_score") is not None:
            source["relevance_score"] = chunk["relevance_score"]

        sources.append(source)

    return sources


# ---------------------------------------------------------------------
# RAG prompt
# ---------------------------------------------------------------------

def create_rag_prompt(
    question: str,
    retrieved_chunks: List[Dict[str, Any]],
) -> str:
    """
    Construct the prompt containing the question and retrieved context.

    Existing behavior is preserved: when no document context is found,
    the model is instructed not to invent document citations.
    """
    context = build_context(retrieved_chunks)

    if not context:
        return f"""
You are an AI assistant.

The user has asked a question, but no relevant document content
was retrieved.

Do not invent document content or citations.

If the question requires information from the uploaded document,
clearly explain that no relevant information was found.

Question:
{question}

Answer:
""".strip()

    return f"""
You are an AI assistant that answers questions using the provided
document context.

Instructions:
1. Answer the user's exact question directly, starting with the answer rather than an introduction.
2. Use only facts supported by the supplied document context. Do not use outside knowledge or invent examples, quotations, facts, pages, or citations.
3. If the context does not contain enough information to answer, respond exactly: "No relevant information was found in the selected document(s) to answer your question."
4. Explain ideas in simple, understandable language. Include the detail the question needs; do not force a short answer.
5. Use short paragraphs and bullets when they make the answer easier to follow. Avoid repeated headings and unnecessary introductions.
6. Use examples only when the document supports them.
7. Cite factual claims with the exact source markers shown in the context, such as [1]. Use only markers that support the claim, and avoid repeating a marker unnecessarily.
8. Use headings only when the answer is long enough to benefit from them.

Document Context:
{context}

User Question:
{question}

Answer:
""".strip()
