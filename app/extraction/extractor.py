"""Raw text extraction for TXT and PDF documents.

Milestone 1 only needs raw text. There is no AI, no LLM calls, and no
guideline matching here - just deterministic text extraction.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from app.extraction.validation import get_document_type
from app.models.document import DocumentType, ExtractedDocument


def extract_txt(data: bytes) -> tuple[str, int]:
    """Extract text from raw TXT bytes.

    Decoding is attempted as UTF-8 first, then falls back to latin-1 so that
    we never fail on unexpected byte sequences (latin-1 maps every byte).

    Returns:
        A tuple of (text, page_count). TXT is always a single "page".
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return text, 1


def extract_pdf(data: bytes) -> tuple[str, int]:
    """Extract text from raw PDF bytes using PyMuPDF.

    Returns:
        A tuple of (text, page_count). Page texts are joined with newlines.
    """
    text_parts: list[str] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        page_count = doc.page_count
        for page in doc:
            text_parts.append(page.get_text())
    return "\n".join(text_parts), page_count


# Sentinel used to join per-page text into a single string while preserving
# page boundaries (so evidence extraction can recover the source page).
PAGE_DELIMITER = "\f"


def extract_pages_from_bytes(filename: str, data: bytes) -> list[str]:
    """Return per-page text for a document (1 entry per page).

    TXT documents are a single page. PDF pages are returned individually. This
    is additive and does not change existing extraction behavior.
    """
    doc_type = get_document_type(filename)
    if doc_type is DocumentType.TXT:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        return [text]

    pages: list[str] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            pages.append(page.get_text())
    return pages or [""]


def join_pages(pages: list[str]) -> str:
    """Join per-page texts with the page delimiter for page-aware storage."""
    return PAGE_DELIMITER.join(pages)


def extract_text_from_bytes(filename: str, data: bytes) -> ExtractedDocument:
    """Validate and extract text from in-memory file bytes.

    This is the primary entry point used by the Streamlit UI, which receives
    uploads as bytes rather than file paths.

    Args:
        filename: Original file name (used for type detection).
        data: Raw file content.

    Returns:
        A populated :class:`ExtractedDocument`.

    Raises:
        ValidationError / UnsupportedFileTypeError: for unsupported files.
    """
    doc_type = get_document_type(filename)

    if doc_type is DocumentType.TXT:
        text, page_count = extract_txt(data)
    else:  # DocumentType.PDF
        text, page_count = extract_pdf(data)

    return ExtractedDocument(
        filename=filename,
        document_type=doc_type,
        text=text,
        page_count=page_count,
    )


def extract_text(path: str | Path) -> ExtractedDocument:
    """Validate and extract text from a file on disk.

    Convenience wrapper around :func:`extract_text_from_bytes` for tests and
    CLI usage.

    Args:
        path: Path to a .txt or .pdf file.

    Returns:
        A populated :class:`ExtractedDocument`.

    Raises:
        FileNotFoundError: If the path does not exist.
        ValidationError / UnsupportedFileTypeError: for unsupported files.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"No such file: {p}")

    data = p.read_bytes()
    return extract_text_from_bytes(p.name, data)
