"""Public response projections for SQLite rows."""


def public_document(document: dict) -> dict:
    keys = (
        "id", "conversation_id", "filename", "file_type", "file_size",
        "status", "total_pages", "total_chunks", "created_at",
    )
    return {key: document.get(key) for key in keys}


def public_conversation(conversation: dict) -> dict:
    keys = (
        "id", "title", "created_at", "updated_at", "document_count",
        "message_count",
    )
    return {key: conversation.get(key) for key in keys if key in conversation}
