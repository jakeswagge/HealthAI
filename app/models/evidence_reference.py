"""Pydantic model for a single piece of source-backed evidence.

An :class:`EvidenceReference` ties a normalized fact (e.g. "diagnosis =
Rheumatoid Arthritis") back to the exact document, page, section, and quoted
text it came from. These references are the backbone of traceability: every
extracted field, review criterion, and appeal statement can point at one or
more evidence ids.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_evidence_id() -> str:
    return f"EV-{uuid.uuid4().hex[:12].upper()}"


class EvidenceReference(BaseModel):
    """A source-backed fact extracted from a specific document/page."""

    evidence_id: str = Field(default_factory=new_evidence_id)
    case_id: str = Field(..., description="Owning case id.")
    source_document_id: str = Field(..., description="CaseDocument.document_id.")
    source_filename: Optional[str] = Field(
        default=None, description="Source filename (denormalized for display/export)."
    )
    page_number: int = Field(default=1, ge=0, description="1-indexed source page.")
    section_label: Optional[str] = Field(
        default=None, description="Section/label the fact was found under."
    )
    quoted_text: str = Field(
        default="", description="Verbatim snippet supporting the fact."
    )
    normalized_fact: str = Field(
        default="", description="Normalized 'field: value' representation."
    )
    fact_type: Optional[str] = Field(
        default=None, description="Logical field name, e.g. 'diagnosis'."
    )
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: str = Field(default_factory=_utc_now_iso)

    @field_validator("quoted_text", "normalized_fact", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        return "" if v is None else str(v).strip()

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        if v is None:
            return 0.0
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    def citation(self) -> str:
        """Human-readable citation, e.g. '(clinical_note.pdf, p.4)'."""
        name = self.source_filename or self.source_document_id
        return f"({name}, p.{self.page_number})"
