"""Pydantic models for evidence governance.

Governance lets an organization choose how strictly evidence must be validated
before it is used by review and appeal generation:

- ``GovernanceSettings``      the org-level policy knobs.
- ``ApprovedEvidenceSet``     the result of applying that policy to a case's
                              evidence (what is included vs. excluded, and why).
- ``GovernanceComplianceReport`` detects policy violations on a case.

Reviewer authority always wins: rejected evidence can never be included in
validated mode, regardless of other settings.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.models.safety import DEFAULT_CONFIDENCE_THRESHOLD


class EvidenceMode(str, Enum):
    """How evidence is selected for downstream use."""

    DRAFT = "DRAFT"          # all evidence allowed (current default behavior)
    VALIDATED = "VALIDATED"  # governance-filtered (approved-only, quality gate)


class GovernanceSettings(BaseModel):
    """Organization-level governance policy."""

    validated_evidence_mode: bool = Field(
        default=False,
        description="When True, downstream consumers use the filtered evidence set.",
    )
    allow_unreviewed_evidence: bool = Field(
        default=True,
        description=(
            "In validated mode, whether evidence with no reviewer decision is "
            "allowed (True) or must be explicitly approved (False)."
        ),
    )
    minimum_quality_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum evidence quality overall_score to be included.",
    )
    require_conflict_resolution: bool = Field(
        default=False,
        description="Require all detected conflicts to be resolved before export.",
    )
    require_human_review_before_export: bool = Field(
        default=True,
        description="Require a human-review decision before a case may be exported.",
    )
    confidence_threshold: float = Field(
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="Pilot threshold below which artifacts require human review.",
    )
    block_autonomous_denials: bool = Field(
        default=True,
        description="Prevent AI/local recommendations from being exported as denials without human sign-off.",
    )
    require_verified_appeal_claims: bool = Field(
        default=True,
        description="Require appeal claims to be verified against source evidence before export.",
    )

    @field_validator("minimum_quality_score", mode="before")
    @classmethod
    def _coerce_min(cls, v):
        if v is None:
            return 0.0
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    @field_validator("confidence_threshold", mode="before")
    @classmethod
    def _coerce_threshold(cls, v):
        if v is None:
            return DEFAULT_CONFIDENCE_THRESHOLD
        try:
            f = float(v)
        except (TypeError, ValueError):
            return DEFAULT_CONFIDENCE_THRESHOLD
        return max(0.0, min(1.0, f))

    @property
    def mode(self) -> EvidenceMode:
        return EvidenceMode.VALIDATED if self.validated_evidence_mode else EvidenceMode.DRAFT


class ExcludedEvidence(BaseModel):
    """An excluded evidence reference and the reason it was filtered out."""

    evidence_id: str
    fact_type: str | None = None
    value: str = ""
    reason: str = ""


class ApprovedEvidenceSet(BaseModel):
    """The governance-filtered evidence selection for a case."""

    case_id: str
    mode: EvidenceMode
    included_ids: list[str] = Field(default_factory=list)
    excluded: list[ExcludedEvidence] = Field(default_factory=list)
    settings_snapshot: dict = Field(default_factory=dict)

    @property
    def included_count(self) -> int:
        return len(self.included_ids)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)

    def is_included(self, evidence_id: str) -> bool:
        return evidence_id in set(self.included_ids)


class ComplianceViolation(BaseModel):
    """A single governance-compliance violation."""

    code: str = Field(..., description="Stable violation code.")
    severity: str = Field(default="MEDIUM", description="HIGH / MEDIUM / LOW.")
    description: str = Field(default="")
    evidence_ids: list[str] = Field(default_factory=list)


class GovernanceComplianceReport(BaseModel):
    """Result of checking a case against governance policy."""

    case_id: str
    mode: EvidenceMode = EvidenceMode.DRAFT
    violations: list[ComplianceViolation] = Field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        return len(self.violations) == 0

    def by_severity(self, severity: str) -> list[ComplianceViolation]:
        return [v for v in self.violations if v.severity == severity]
