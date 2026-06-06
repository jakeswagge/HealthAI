"""Pydantic models for governance-aware explainability (Milestone 13).

These models make the reasoning behind a generated review or appeal auditable
and traceable:

- :class:`EvidenceLineage`     one row of the traceability chain: an evidence
                               id linked back to its source document, page,
                               reviewer decision, and quality score.
- :class:`ReviewExplanation`   why a review reached its recommendation, which
                               evidence it used vs. excluded, and the governance
                               mode in force.
- :class:`AppealExplanation`   the same, for a generated appeal letter.
- :class:`TraceabilityChain`   the full evidence-lineage chain for a case.

Nothing here changes review/appeal behavior; it records *why* an output was
produced and *what evidence was permitted* to produce it. In VALIDATED mode the
``evidence_used`` lists contain only governance-approved evidence; rejected /
excluded evidence appears solely in ``evidence_excluded`` (with a reason) and
never contributes to the recommendation, rationale, confidence, or appeal body.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from app.models.governance import EvidenceMode


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_review_explanation_id() -> str:
    return f"RXP-{uuid.uuid4().hex[:12].upper()}"


def new_appeal_explanation_id() -> str:
    return f"AXP-{uuid.uuid4().hex[:12].upper()}"


class EvidenceLineage(BaseModel):
    """One link in the traceability chain for a single evidence reference."""

    evidence_id: str = Field(..., description="The EvidenceReference id.")
    fact_type: Optional[str] = Field(default=None, description="Logical field name.")
    value: str = Field(default="", description="The normalized fact value.")
    source_document_id: Optional[str] = Field(default=None)
    source_filename: Optional[str] = Field(default=None)
    page_number: Optional[int] = Field(default=None)
    quoted_text: str = Field(default="", description="Verbatim source quote.")
    reviewer_decision: str = Field(
        default="PENDING",
        description="Latest reviewer decision: APPROVE / REJECT / FLAG / PENDING.",
    )
    quality_score: Optional[float] = Field(
        default=None, description="Overall evidence-quality score, if assessed."
    )
    included: bool = Field(
        default=True,
        description="Whether this evidence was permitted for downstream use.",
    )
    exclusion_reason: str = Field(
        default="", description="Why the evidence was excluded (if it was)."
    )

    def citation(self) -> str:
        name = self.source_filename or self.source_document_id or "source"
        if self.page_number:
            return f"({name}, p.{self.page_number})"
        return f"({name})"


class ReviewExplanation(BaseModel):
    """Explainability record for a clinical review result."""

    explanation_id: str = Field(default_factory=new_review_explanation_id)
    review_id: str = Field(
        default="",
        description="Stable id linking to the review (case_id + guideline).",
    )
    case_id: str = Field(default="")
    recommendation: str = Field(default="", description="APPROVE/DENY/INSUFFICIENT.")
    governance_mode: EvidenceMode = Field(default=EvidenceMode.DRAFT)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_used: list[EvidenceLineage] = Field(default_factory=list)
    evidence_excluded: list[EvidenceLineage] = Field(default_factory=list)
    reasoning_steps: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=_utc_now_iso)

    @property
    def evidence_used_ids(self) -> list[str]:
        return [e.evidence_id for e in self.evidence_used]

    @property
    def evidence_excluded_ids(self) -> list[str]:
        return [e.evidence_id for e in self.evidence_excluded]


class AppealExplanation(BaseModel):
    """Explainability record for a generated appeal letter."""

    explanation_id: str = Field(default_factory=new_appeal_explanation_id)
    appeal_id: str = Field(default="")
    case_id: str = Field(default="")
    governance_mode: EvidenceMode = Field(default=EvidenceMode.DRAFT)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_used: list[EvidenceLineage] = Field(default_factory=list)
    evidence_excluded: list[EvidenceLineage] = Field(default_factory=list)
    guideline_support: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    reasoning_steps: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=_utc_now_iso)

    @property
    def evidence_used_ids(self) -> list[str]:
        return [e.evidence_id for e in self.evidence_used]

    @property
    def evidence_excluded_ids(self) -> list[str]:
        return [e.evidence_id for e in self.evidence_excluded]


class TraceabilityChain(BaseModel):
    """The full evidence-lineage chain for a case (used vs. excluded)."""

    case_id: str
    governance_mode: EvidenceMode = Field(default=EvidenceMode.DRAFT)
    links: list[EvidenceLineage] = Field(default_factory=list)
    timestamp: str = Field(default_factory=_utc_now_iso)

    @property
    def included_links(self) -> list[EvidenceLineage]:
        return [link for link in self.links if link.included]

    @property
    def excluded_links(self) -> list[EvidenceLineage]:
        return [link for link in self.links if not link.included]
