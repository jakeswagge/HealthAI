"""Pydantic model for evidence quality assessment.

An :class:`EvidenceQualityAssessment` scores a single :class:`EvidenceReference`
on four dimensions and rolls them into an overall score, plus a list of detected
issues. This lets reviewers prioritize weak/low-quality evidence without
changing the EvidenceReference contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field, computed_field, field_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_assessment_id() -> str:
    return f"EQA-{uuid.uuid4().hex[:12].upper()}"


# Default overall-score threshold below which evidence is considered "weak".
WEAK_EVIDENCE_THRESHOLD = 0.55


def _clamp(v) -> float:
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


class EvidenceQualityAssessment(BaseModel):
    """Quality scoring for a single evidence reference."""

    assessment_id: str = Field(default_factory=new_assessment_id)
    evidence_id: str = Field(..., description="The EvidenceReference scored.")
    case_id: str = Field(default="", description="Owning case id.")
    completeness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    consistency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    traceability_score: float = Field(default=0.0, ge=0.0, le=1.0)
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=_utc_now_iso)

    @field_validator(
        "completeness_score",
        "relevance_score",
        "consistency_score",
        "traceability_score",
        "overall_score",
        mode="before",
    )
    @classmethod
    def _coerce_score(cls, v):
        return _clamp(v)

    @field_validator("issues", mode="before")
    @classmethod
    def _coerce_issues(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(x).strip() for x in v if str(x).strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_weak(self) -> bool:
        """True if overall quality is below the weak-evidence threshold."""
        return self.overall_score < WEAK_EVIDENCE_THRESHOLD
