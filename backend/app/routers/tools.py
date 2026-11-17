
import logging

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.models.schemas import (
    SummaryRequest,
    ExtractRequest,
    CompareRequest,
    SuggestionRequest,
)
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


def _get_document_context(document_ids: list[str], query: str = "", owner_id: str | None = None):
    """Fetch document chunks and prepare context for an AI operation."""

    if not document_ids:
        raise HTTPException(
            status_code=400,
            detail="Please select at least one document.",
        )

    unique_ids = list(dict.fromkeys(document_ids))
    documents = database.get_documents_for_owner(unique_ids, owner_id or "")
    documents_by_id = {document["id"]: document for document in documents}
    for document_id in unique_ids:
        document = documents_by_id.get(document_id)
        if not document:
            raise HTTPException(
                status_code=404,
                detail=f"Document not found: {document_id}",
            )

        if document["status"] != "processed":
            raise HTTPException(
                status_code=400,
                detail=f"Document is not ready: {document.get('filename', document_id)}",
            )


    chunks = database.get_chunks_for_documents(unique_ids)

    if not chunks:
        raise HTTPException(
            status_code=400,
            detail="No readable text found in the selected documents.",
        )

    if query:
        with measure_stage("rag_retrieval"):
            relevant_chunks = retrieve_relevant_chunks(
                query=query,
                chunks=chunks,
                top_k=10,
                document_ids=unique_ids,
            )
    else:
        # For summaries and comparisons, use all available chunks.
        relevant_chunks = chunks[:30]

    context = create_rag_prompt(
        question=query or "Analyze the selected documents.",
        retrieved_chunks=relevant_chunks,
    )

    sources = extract_sources(relevant_chunks)

    return context, sources, documents


async def _generate_tool_response(
    instruction: str,
    context: str,
    request,
):
    """Call the configured LLM provider."""

    from app.services.llm_service import generate_response

    result = await generate_response(
        prompt=f"""
You are an AI document analysis assistant.

Follow these rules:
- Use the provided document context as the primary source.
- Do not invent facts, numbers, dates, or quotations.
- If information is missing, clearly say it is not available.
- Keep the answer structured and easy to understand.

TASK:
{instruction}

DOCUMENT CONTEXT:
{context}
""",
        history=[],
        provider=getattr(request, "provider", None),
        model=getattr(request, "model", None),
        temperature=0.2,
    )

    if isinstance(result, dict):
        answer = result.get("content", "")
    else:
        answer = str(result)

    if not answer.strip():
        raise HTTPException(
            status_code=502,
            detail="The AI provider returned an empty response.",
        )

    return answer


@router.post("/summary")
async def summarize_documents(request: SummaryRequest, user: dict = Depends(get_current_user)):
    try:
        context, sources, documents = await run_in_threadpool(
            _get_document_context,
            request.document_ids,
            "Summarize the selected documents and identify their key points.",
            user["id"],
        )

        instruction = """
Create a comprehensive summary of the selected documents.

Include:
1. Short overview
2. Main topics
3. Important facts and findings
4. Key dates, names, and numbers
5. Important conclusions

Use headings and bullet points.
"""

        summary = await _generate_tool_response(
            instruction,
            context,
            request,
        )

        return {
            "success": True,
            "summary": summary,
            "documents": [
                document["filename"] for document in documents
            ],
            "sources": sources,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Document summarization failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to summarize the selected documents.",
        )


@router.post("/extract")
async def extract_information(request: ExtractRequest, user: dict = Depends(get_current_user)):
    try:
        query = request.query.strip()

        if not query:
            raise HTTPException(
                status_code=400,
                detail="Please provide the information you want to extract.",
            )

        context, sources, documents = await run_in_threadpool(
            _get_document_context,
            request.document_ids,
            query,
            user["id"],
        )

        instruction = f"""
Extract information related to this request:

{query}

Requirements:
1. Return only information supported by the documents.
2. Organize the extracted information clearly.
3. Include relevant names, dates, amounts, and other details.
4. If the information is not found, explicitly say so.
"""

        extracted = await _generate_tool_response(
            instruction,
            context,
            request,
        )

        return {
            "success": True,
            "query": query,
            "result": extracted,
            "documents": [
                document["filename"] for document in documents
            ],
            "sources": sources,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Information extraction failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to extract information from the documents.",
        )


@router.post("/compare")
async def compare_documents(request: CompareRequest, user: dict = Depends(get_current_user)):
    try:
        document_ids = list(dict.fromkeys(request.document_ids))

        if len(document_ids) < 2:
            raise HTTPException(
                status_code=400,
                detail="Select at least two documents to compare.",
            )

        context, sources, documents = await run_in_threadpool(
            _get_document_context,
            document_ids,
            "Compare the selected documents and identify their similarities and differences.",
            user["id"],
        )

        document_names = [
            document["filename"] for document in documents
        ]

        instruction = f"""
Compare these documents:

{chr(10).join(f"- {name}" for name in document_names)}

Provide:
1. Overview of each document
2. Similarities
3. Differences
4. Important conflicting information
5. Key dates, numbers, and facts
6. Final comparison and conclusion

Use a Markdown table where appropriate.
Do not assume that missing information means the documents disagree.
"""

        comparison = await _generate_tool_response(
            instruction,
            context,
            request,
        )

        return {
            "success": True,
            "comparison": comparison,
            "documents": document_names,
            "sources": sources,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Document comparison failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to compare the selected documents.",
        )


@router.post("/suggestions")
async def generate_suggestions(request: SuggestionRequest, user: dict = Depends(get_current_user)):
    try:
        context, sources, documents = await run_in_threadpool(
            _get_document_context,
            request.document_ids,
            "Identify the main topics, important facts, and useful questions from these documents.",
            user["id"],
        )

        instruction = """
Generate 8 useful questions that a user can ask about these documents.

Include a mixture of:
- General understanding questions
- Important fact-based questions
- Detailed analysis questions
- Questions about dates, figures, and key findings

Rules:
- Questions must be answerable from the provided documents.
- Avoid duplicate questions.
- Return only a numbered list of questions.
"""

        suggestions = await _generate_tool_response(
            instruction,
            context,
            request,
        )

        return {
            "success": True,
            "suggestions": suggestions,
            "documents": [
                document["filename"] for document in documents
            ],
            "sources": sources,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Question suggestion generation failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to generate question suggestions.",
        )
