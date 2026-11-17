
import logging
import re
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.models.schemas import ExportRequest
from app.services import database
from app.routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


def _safe_filename(value: str) -> str:
    """Create a safe filename."""
    value = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", value)
    value = re.sub(r"\s+", "_", value.strip()).strip("._")
    return value[:80] or "conversation"


def _get_export_content(request: ExportRequest, owner_id: str):
    conversation = database.get_conversation(request.conversation_id)

    if not conversation or conversation.get("owner_id") != owner_id:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    messages = database.get_messages(request.conversation_id)

    if not messages:
        raise HTTPException(
            status_code=400,
            detail="No messages available to export.",
        )

    title = conversation.get("title", "Conversation")

    lines = [
        f"# {title}",
        "",
        f"Conversation ID: {request.conversation_id}",
        "",
        "---",
        "",
    ]

    rendered_messages = []
    for message in messages:
        role = str(message.get("role", "assistant")).capitalize()
        content = message.get("content", "")
        sources = _format_sources(message.get("sources", [])) if request.include_sources else []
        text_content = str(content)
        if sources:
            text_content += "\n\nSources: " + "; ".join(sources)
        rendered_messages.append({**message, "export_content": text_content, "export_sources": sources})

        lines.extend([
            f"## {role}",
            "",
            text_content,
            "",
            "---",
            "",
        ])

    return title, rendered_messages, "\n".join(lines)


def _format_sources(sources: list) -> list[str]:
    """Return unique, verifiable filename/page labels for a message."""
    labels = []
    seen = set()
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        filename = str(source.get("filename") or "").strip()
        if not filename:
            continue
        page = source.get("page_number")
        page = page if type(page) is int and page > 0 else None
        key = (source.get("document_id") or filename, page)
        if key in seen:
            continue
        seen.add(key)
        marker = f"[{len(labels) + 1}] "
        label = f"{filename} (p. {page})" if page is not None else filename
        labels.append(f"{marker}{label}")
    return labels


def _create_txt(content: str) -> bytes:
    return content.encode("utf-8")


def _create_markdown(content: str) -> bytes:
    return content.encode("utf-8")


def _create_docx(title: str, messages: list) -> bytes:
    from docx import Document

    document = Document()
    document.add_heading(title, level=0)

    for message in messages:
        role = str(message.get("role", "assistant")).capitalize()
        content = str(message.get("export_content", message.get("content", "")))

        document.add_heading(role, level=2)

        for paragraph in content.split("\n"):
            document.add_paragraph(paragraph)

    output = BytesIO()
    document.save(output)
    output.seek(0)

    return output.getvalue()


def _create_pdf(title: str, messages: list) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        HRFlowable,
    )
    from xml.sax.saxutils import escape

    output = BytesIO()

    pdf = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ExportTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        spaceAfter=20,
    )

    role_style = ParagraphStyle(
        "RoleHeading",
        parent=styles["Heading2"],
        textColor=colors.HexColor("#2563EB"),
        spaceBefore=12,
        spaceAfter=6,
    )

    body_style = ParagraphStyle(
        "MessageBody",
        parent=styles["BodyText"],
        leading=15,
        spaceAfter=5,
        wordWrap="CJK",
    )

    story = [Paragraph(escape(title), title_style)]

    for message in messages:
        role = str(message.get("role", "assistant")).capitalize()
        content = str(message.get("content", ""))

        story.append(Paragraph(escape(role), role_style))

        for line in content.splitlines():
            safe_line = escape(line) if line.strip() else " "
            story.append(Paragraph(safe_line, body_style))

        source_labels = message.get("export_sources", [])
        if source_labels:
            story.append(Paragraph("Sources", styles["Heading4"]))
            for source in source_labels:
                story.append(Paragraph(escape(str(source)), body_style))

        story.append(Spacer(1, 8))
        story.append(HRFlowable(width="100%", thickness=0.5))
        story.append(Spacer(1, 8))

    pdf.build(story)
    output.seek(0)

    return output.getvalue()


@router.post("/{file_format}")
def export_conversation(
    file_format: str,
    request: ExportRequest,
    user: dict = Depends(get_current_user),
):
    file_format = file_format.lower().strip()

    supported_formats = {
        "txt",
        "md",
        "markdown",
        "docx",
        "pdf",
    }

    if file_format not in supported_formats:
        raise HTTPException(
            status_code=400,
            detail="Unsupported format. Use pdf, docx, txt, or md.",
        )

    try:
        title, messages, content = _get_export_content(request, user["id"])
        filename = _safe_filename(title)
        extension = file_format if file_format != "markdown" else "md"
        download_name = f"{filename}.{extension}"
        ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", download_name)

        if file_format == "txt":
            file_data = _create_txt(content)
            media_type = "text/plain"
            extension = "txt"

        elif file_format in {"md", "markdown"}:
            file_data = _create_markdown(content)
            media_type = "text/markdown"
            extension = "md"

        elif file_format == "docx":
            file_data = _create_docx(title, messages)
            media_type = (
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            )
            extension = "docx"

        else:
            file_data = _create_pdf(title, messages)
            media_type = "application/pdf"
            extension = "pdf"

        return Response(
            content=file_data,
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{ascii_name}"; '
                    f"filename*=UTF-8''{quote(download_name, safe='')}"
                )
            },
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("Conversation export failed")

        raise HTTPException(
            status_code=500,
            detail="Unable to export conversation.",
        )
