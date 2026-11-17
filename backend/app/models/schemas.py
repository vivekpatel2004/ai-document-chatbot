
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# -------------------------
# Common API Response
# -------------------------

class APIResponse(BaseModel):
    success: bool = True
    message: str = ""
    data: Any = None


# -------------------------
# Document Schemas
# -------------------------

class DocumentResponse(BaseModel):
    id: str
    conversation_id: str | None = None
    filename: str
    file_type: str
    file_size: int = 0
    status: str = "processed"
    total_pages: int = 0
    total_chunks: int = 0
    created_at: datetime | str | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse] = Field(default_factory=list)
    total: int = 0


# -------------------------
# Conversation Schemas
# -------------------------

class ConversationCreate(BaseModel):
    title: str = Field(default="New Chat", min_length=1, max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    document_count: int = 0
    message_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse] = Field(default_factory=list)
    total: int = 0


# -------------------------
# Chat Schemas
# -------------------------

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    provider: Literal["gemini", "openai"] | None = None
    model: str | None = None
    temperature: float = Field(default=0.2, ge=0, le=2)
    stream: bool = False


class SourceReference(BaseModel):
    document_id: str
    filename: str
    page_number: int | None = None
    chunk_id: str | None = None
    relevance_score: float | None = None
    excerpt: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    sources: list[SourceReference] = Field(default_factory=list)
    created_at: datetime | str | None = None


# -------------------------
# Message History Schemas
# -------------------------

class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    sources: list[SourceReference] = Field(default_factory=list)
    created_at: datetime | str | None = None

    model_config = ConfigDict(from_attributes=True)


class MessageListResponse(BaseModel):
    messages: list[MessageResponse] = Field(default_factory=list)
    total: int = 0


# -------------------------
# Document Selection
# -------------------------

class DocumentSelection(BaseModel):
    document_ids: list[str] = Field(default_factory=list)


# -------------------------
# AI Document Tools
# -------------------------

class SummaryRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1)
    instructions: str | None = None


class ExtractRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2000)
    instructions: str | None = None


class CompareRequest(BaseModel):
    document_ids: list[str] = Field(min_length=2)
    comparison_criteria: list[str] = Field(default_factory=list)
    instructions: str | None = None


class SuggestionRequest(BaseModel):
    conversation_id: str | None = None
    document_ids: list[str] = Field(default_factory=list)


# -------------------------
# Export Schemas
# -------------------------

class ExportRequest(BaseModel):
    conversation_id: str
    format: Literal["pdf", "docx", "txt", "md"] = "pdf"
    include_sources: bool = True


# -------------------------
# Error Response
# -------------------------

class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: str | None = None
