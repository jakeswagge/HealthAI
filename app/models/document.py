"""Pydantic models describing uploaded documents and their extracted text.

These models are intentionally small for Milestone 1. They give us a typed,
validated container for the result of text extraction so the UI and tests can
rely on a stable shape.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, computed_field


class DocumentType(str, Enum):
    """Supported document types for Milestone 1."""

    PDF = "pdf"
    TXT = "txt"


# Map of accepted file extensions (lowercase, no dot) to their document type.
SUPPORTED_EXTENSIONS: dict[str, DocumentType] = {
    "pdf": DocumentType.PDF,
    "txt": DocumentType.TXT,
}


class ExtractedDocument(BaseModel):
    """The result of extracting raw text from an uploaded document."""

    filename: str = Field(..., description="Original file name as uploaded.")
    document_type: DocumentType = Field(
        ..., description="Detected document type (pdf or txt)."
    )
    text: str = Field(
        default="", description="Raw text extracted from the document."
    )
    page_count: int = Field(
        default=1,
        ge=0,
        description="Number of pages. TXT files are treated as a single page.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def char_count(self) -> int:
        """Number of characters in the extracted text."""
        return len(self.text)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def word_count(self) -> int:
        """Number of whitespace-separated words in the extracted text."""
        return len(self.text.split())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_empty(self) -> bool:
        """True when no meaningful text was extracted."""
        return len(self.text.strip()) == 0
