"""Pydantic model for a single document attached to a case.

A case may contain many supporting documents (denial letter, clinical notes,
referral, lab results, imaging report, prior-auth form, ...). Each is stored as
a :class:`CaseDocument` with its raw text so evidence can be traced back to a
specific document and page.

Page boundaries are preserved inside ``raw_text`` using the form-feed
character (``\\f``) as a page delimiter, so evidence extraction can report an
accurate ``page_number`` without a separate per-page table.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator

# Page delimiter embedded in raw_text to preserve page boundaries.
PAGE_DELIMITER = "\f"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_document_id() -> str:
    return f"DOC-{uuid.uuid4().hex[:12].upper()}"


class DocumentCategory(str, Enum):
    """The kind of supporting document."""

    DENIAL_LETTER = "DENIAL_LETTER"
    CLINICAL_NOTE = "CLINICAL_NOTE"
    REFERRAL = "REFERRAL"
    LAB_RESULT = "LAB_RESULT"
    IMAGING_REPORT = "IMAGING_REPORT"
    PRIOR_AUTH_FORM = "PRIOR_AUTH_FORM"
    OTHER = "OTHER"


# Keyword hints used to classify a document by filename / content.
_CATEGORY_HINTS: list[tuple[DocumentCategory, tuple[str, ...]]] = [
    (DocumentCategory.DENIAL_LETTER, ("denial", "adverse determination", "denied", "notice of adverse")),
    (DocumentCategory.PRIOR_AUTH_FORM, ("prior auth", "prior-auth", "priorauth", "authorization request", "pa form", "pa_form")),
    (DocumentCategory.IMAGING_REPORT, ("mri", "imaging", "radiology", "ct scan", "ct chest", "x-ray", "xray", "ultrasound", "radiograph")),
    (DocumentCategory.LAB_RESULT, ("lab", "laboratory", "blood test", "panel", "specimen", "reference range")),
    (DocumentCategory.REFERRAL, ("referral", "refer to", "referred by", "consult request")),
    (DocumentCategory.CLINICAL_NOTE, ("clinical note", "progress note", "physician note", "office visit", "h&p", "history and physical", "soap note")),
]


def classify_document(filename: str | None, text: str | None) -> DocumentCategory:
    """Infer the document category from filename then content.

    Filename hints take precedence (they are usually intentional), then a
    content scan. Falls back to ``OTHER``.
    """
    name = (filename or "").lower()
    body = (text or "").lower()

    for category, hints in _CATEGORY_HINTS:
        if any(h in name for h in hints):
            return category
    for category, hints in _CATEGORY_HINTS:
        if any(h in body for h in hints):
            return category
    return DocumentCategory.OTHER


class CaseDocument(BaseModel):
    """A single supporting document belonging to a case."""

    document_id: str = Field(default_factory=new_document_id)
    case_id: str = Field(..., description="Owning case id.")
    filename: str = Field(..., description="Original filename.")
    document_type: DocumentCategory = Field(default=DocumentCategory.OTHER)
    uploaded_at: str = Field(default_factory=_utc_now_iso)
    page_count: int = Field(default=1, ge=0)
    raw_text: str = Field(default="", description="Extracted text (pages joined by \\f).")

    @field_validator("document_type", mode="before")
    @classmethod
    def _coerce_type(cls, v):
        if isinstance(v, DocumentCategory):
            return v
        if v is None:
            return DocumentCategory.OTHER
        try:
            return DocumentCategory(str(v).strip().upper())
        except ValueError:
            return DocumentCategory.OTHER

    @computed_field  # type: ignore[prop-decorator]
    @property
    def char_count(self) -> int:
        return len(self.raw_text)

    def pages(self) -> list[str]:
        """Return per-page text using the embedded page delimiter."""
        if not self.raw_text:
            return [""]
        return self.raw_text.split(PAGE_DELIMITER)

    def page_text(self, page_number: int) -> str:
        """Return the text of a 1-indexed page (empty if out of range)."""
        pages = self.pages()
        if 1 <= page_number <= len(pages):
            return pages[page_number - 1]
        return ""
