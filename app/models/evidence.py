"""Pydantic models for evidence traceability.

An :class:`EvidenceReference` ties a normalized fact back to the exact source:
which document, which page, the section label, and the verbatim quoted text.
This is the backbone of traceability across extraction, review, and appeals -
it lets the system answer "what document/page did this come from?" and "what
evidence supports this recommendation?".

A :class:`ConflictReport` captures disagreements between documents (e.g. two
different member IDs), each with a severity level.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceReference(BaseModel):
    """A single source-backed piece of evidence."""

    evidence_id: str = Field(
        default_factory=lambda: f"EV-{uuid.uuid4().hex[:12].upper()}"
    )
    case_id: str = Field(..., description="Owning case id.")
    source_document_id: str = Field(..., description="CaseDocument.document_id.")
    page_number: Optional[int] = Field(
        default=None, description="1-based source page, if known."
    )
    section_label: Optional[str] = Field(
        default=None, description="Section/heading the evidence was found under."
    )
    quoted_text: str = Field(
        default="", description="Verbatim text quoted from the source."
    )
    normalized_fact: str = Field(
        default="",
        description="The normalized fact this evidence supports (e.g. 'diagnosis: RA').",
    )
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: str = Field(default_factory=_utc_now_iso)

    # Optional provenance helpers (non-core; aid display/export).
    source_filename: Optional[str] = Field(default=None)
    field_name: Optional[str] = Field(
        default=None, description="The structured field this evidence backs."
    )

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        if v is None:
            return 0.0
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("quoted_text", "normalized_fact", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        return "" if v is None else str(v).strip()

    def citation(self) -> str:
        """A short human-readable citation, e.g. '(clinical_note.pdf, p.4)'."""
        name = self.source_filename or self.source_document_id
        if self.page_number:
            return f"({name}, p.{self.page_number})"
        return f"({name})"


class ConflictSeverity(str, Enum):
    """Severity of a detected conflict between documents."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FieldConflict(BaseModel):
    """A single conflicting fact across documents."""

    field_name: str = Field(..., description="The field in conflict (e.g. member_id).")
    severity: ConflictSeverity = Field(default=ConflictSeverity.MEDIUM)
    description: str = Field(default="")
    # Each value mapped to the evidence references that asserted it.
    values: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, v):
        if isinstance(v, ConflictSeverity):
            return v
        return ConflictSeverity(str(v).strip().upper())


class ConflictReport(BaseModel):
    """All detected conflicts for a case."""

    case_id: str
    conflicts: list[FieldConflict] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utc_now_iso)

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0

    @property
    def highest_severity(self) -> Optional[ConflictSeverity]:
        order = {ConflictSeverity.LOW: 0, ConflictSeverity.MEDIUM: 1, ConflictSeverity.HIGH: 2}
        if not self.conflicts:
            return None
        return max((c.severity for c in self.conflicts), key=lambda s: order[s])
