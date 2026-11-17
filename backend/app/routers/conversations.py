
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.schemas import (
    ConversationCreate,
    ConversationRename,
)
from app.services import database
from app.services.serialization import public_conversation, public_document
from app.routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_conversation(request: ConversationCreate, user: dict = Depends(get_current_user)):
    conversation_id = str(uuid.uuid4())
    title = request.title.strip()

    if not title:
        title = "New Chat"

    database.create_conversation(
        conversation_id=conversation_id,
        title=title,
        owner_id=user["id"],
    )

    conversation = database.get_conversation(conversation_id)

    return {
        "success": True,
        "message": "Conversation created successfully.",
        "conversation": public_conversation(conversation),
    }


@router.get("/")
def list_conversations(user: dict = Depends(get_current_user)):
    conversations = database.list_conversations(user["id"])

    return {
        "success": True,
        "conversations": [public_conversation(conversation) for conversation in conversations],
        "total": len(conversations),
    }


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str, user: dict = Depends(get_current_user)):
    conversation = database.get_conversation(conversation_id)

    if not conversation or conversation.get("owner_id") != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    return {
        "success": True,
        "conversation": public_conversation(conversation),
    }


@router.patch("/{conversation_id}")
def rename_conversation(
    conversation_id: str,
    request: ConversationRename,
    user: dict = Depends(get_current_user),
):
    conversation = database.get_conversation(conversation_id)

    if not conversation or conversation.get("owner_id") != user["id"]:
        raise HTTPException(404, "Conversation not found.")

    title = request.title.strip()

    if not title:
        raise HTTPException(400, "Conversation title cannot be empty.")

    database.rename_conversation(
        conversation_id,
        title,
    )

    return {
        "success": True,
        "message": "Conversation renamed successfully.",
        "conversation": public_conversation(database.get_conversation(conversation_id)),
    }


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, user: dict = Depends(get_current_user)):
    conversation = database.get_conversation(conversation_id)

    if not conversation or conversation.get("owner_id") != user["id"]:
        raise HTTPException(404, "Conversation not found.")

    # Documents belong to the user and remain in their library when a chat is
    # deleted. SQLite clears their legacy conversation_id with ON DELETE SET NULL.
    database.delete_conversation(conversation_id, user["id"])

    return {
        "success": True,
        "message": "Conversation deleted successfully.",
        "conversation_id": conversation_id,
    }


@router.delete("/{conversation_id}/messages")
def clear_conversation_messages(conversation_id: str, user: dict = Depends(get_current_user)):
    conversation = database.get_conversation(conversation_id)

    if not conversation or conversation.get("owner_id") != user["id"]:
        raise HTTPException(404, "Conversation not found.")

    database.clear_conversation(conversation_id)

    return {
        "success": True,
        "message": "Conversation messages cleared successfully.",
        "conversation_id": conversation_id,
    }


@router.get("/{conversation_id}/messages")
def get_conversation_messages(conversation_id: str, user: dict = Depends(get_current_user)):
    conversation = database.get_conversation(conversation_id)

    if not conversation or conversation.get("owner_id") != user["id"]:
        raise HTTPException(404, "Conversation not found.")

    messages = database.get_messages(conversation_id)

    return {
        "success": True,
        "conversation_id": conversation_id,
        "messages": messages,
        "total": len(messages),
    }


@router.get("/{conversation_id}/documents")
def get_conversation_documents(conversation_id: str, user: dict = Depends(get_current_user)):
    exists, documents = database.get_conversation_documents_for_owner(
        conversation_id, user["id"]
    )
    if not exists:
        raise HTTPException(404, "Conversation not found.")

    return {
        "success": True,
        "conversation_id": conversation_id,
        "documents": [public_document(document) for document in documents],
        "total": len(documents),
    }
