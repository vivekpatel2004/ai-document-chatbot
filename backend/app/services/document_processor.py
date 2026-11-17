
import re
import uuid
from pathlib import Path
from typing import Any

import fitz
from docx import Document as DocxDocument


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200


class DocumentProcessingError(Exception):
    """Raised when a document cannot be processed."""


def validate_file(
    filename: str,
    file_size: int,
) -> tuple[bool, str]:
    if not filename or not filename.strip():
        return False, "Filename is required."

    extension = Path(filename).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        return False, (
            f"Unsupported file format: {extension}. "
            "Supported formats: PDF, DOCX, TXT."
        )

    if file_size <= 0:
        return False, "File is empty."

    if file_size > MAX_FILE_SIZE:
        return False, "File exceeds the maximum size of 25 MB."

    return True, "File is valid."


def clean_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\x00", " ")
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    # Normalize excessive whitespace without removing paragraphs.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def extract_pdf(file_path: str | Path) -> list[dict[str, Any]]:
    pages = []

    try:
        with fitz.open(str(file_path)) as pdf:
            if pdf.is_encrypted:
                raise DocumentProcessingError(
                    "Password-protected PDFs are not supported."
                )

            for page_index, page in enumerate(pdf):
                text = clean_text(page.get_text("text"))

                if text:
                    pages.append({
                        "page_number": page_index + 1,
                        "text": text,
                    })

    except DocumentProcessingError:
        raise
    except Exception as exc:
        raise DocumentProcessingError(
            f"Unable to read PDF: {exc}"
        ) from exc

    if not pages:
        raise DocumentProcessingError(
            "No readable text found in PDF. "
            "Scanned PDFs require OCR support."
        )

    return pages


def extract_docx(file_path: str | Path) -> list[dict[str, Any]]:
    try:
        document = DocxDocument(str(file_path))

        content = []

        for paragraph in document.paragraphs:
            text = clean_text(paragraph.text)

            if text:
                content.append(text)

        # Extract table content too.
        for table in document.tables:
            for row in table.rows:
                cells = [
                    clean_text(cell.text)
                    for cell in row.cells
                ]

                cells = [cell for cell in cells if cell]

                if cells:
                    content.append(" | ".join(cells))

        full_text = "\n\n".join(content)

        if not full_text:
            raise DocumentProcessingError(
                "No readable text found in DOCX."
            )

        return [{
            "page_number": None,
            "text": full_text,
        }]

    except DocumentProcessingError:
        raise
    except Exception as exc:
        raise DocumentProcessingError(
            f"Unable to read DOCX: {exc}"
        ) from exc


def extract_txt(file_path: str | Path) -> list[dict[str, Any]]:
    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]

    for encoding in encodings:
        try:
            text = Path(file_path).read_text(encoding=encoding)
            text = clean_text(text)

            if not text:
                raise DocumentProcessingError(
                    "Text file is empty."
                )

            return [{
                "page_number": None,
                "text": text,
            }]

        except UnicodeDecodeError:
            continue
        except DocumentProcessingError:
            raise
        except Exception as exc:
            raise DocumentProcessingError(
                f"Unable to read TXT: {exc}"
            ) from exc

    raise DocumentProcessingError(
        "Unable to decode text file."
    )


def extract_document(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path)

    if not path.exists() or not path.is_file():
        raise DocumentProcessingError(
            "Document file does not exist."
        )

    extension = path.suffix.lower()

    if extension == ".pdf":
        pages = extract_pdf(path)

    elif extension == ".docx":
        pages = extract_docx(path)

    elif extension == ".txt":
        pages = extract_txt(path)

    else:
        raise DocumentProcessingError(
            f"Unsupported document format: {extension}"
        )

    return {
        "document_id": str(uuid.uuid4()),
        "filename": path.name,
        "file_type": extension.lstrip("."),
        "total_pages": len(pages) if extension == ".pdf" else 0,
        "total_characters": sum(
            len(page["text"]) for page in pages
        ),
        "pages": pages,
    }


def split_text_into_chunks(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero.")

    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError(
            "chunk_overlap must be non-negative "
            "and smaller than chunk_size."
        )

    text = clean_text(text)

    if not text:
        return []

    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)

        # Prefer splitting at a sentence or paragraph boundary.
        if end < text_length:
            search_start = start + int(chunk_size * 0.7)

            boundary = max(
                text.rfind("\n\n", search_start, end),
                text.rfind(". ", search_start, end),
                text.rfind("! ", search_start, end),
                text.rfind("? ", search_start, end),
            )

            if boundary > start:
                end = boundary + 1

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= text_length:
            break

        next_start = max(start + 1, end - chunk_overlap)

        # Avoid starting the next chunk in the middle of a word.
        while (
            next_start < end
            and text[next_start].isalnum()
            and text[next_start - 1].isalnum()
        ):
            next_start += 1

        start = next_start

    return chunks


def create_document_chunks(
    pages: list[dict[str, Any]],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict[str, Any]]:

    results = []

    for page in pages:
        page_chunks = split_text_into_chunks(
            page["text"],
            chunk_size,
            chunk_overlap,
        )

        for chunk_index, chunk_text in enumerate(page_chunks):
            results.append({
                "chunk_id": str(uuid.uuid4()),
                "chunk_index": len(results),
                "page_number": page.get("page_number"),
                "content": chunk_text,
            })

    return results


def process_document(file_path: str | Path) -> dict[str, Any]:
    extracted = extract_document(file_path)

    chunks = create_document_chunks(
        extracted["pages"]
    )

    if not chunks:
        raise DocumentProcessingError(
            "Document could not produce searchable text chunks."
        )

    extracted["chunks"] = chunks
    extracted["total_chunks"] = len(chunks)

    return extracted
