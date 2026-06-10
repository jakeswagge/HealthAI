"""Pydantic models for clinical review output.

A :class:`ReviewResult` is the validated output of the clinical review engine
(deterministic or Claude-backed). It explains whether a requested service meets
guideline criteria, what is missing, and what to do next.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


class Recommendation(str, Enum):
    """Outcome of a clinical review."""

    APPROVE = "APPROVE"
    DENY = "DENY"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


class CriterionStatus(str, Enum):
    """Normalized status for one guideline criterion."""

    MET = "met"
    NOT_MET = "not_met"
    UNKNOWN = "unknown"


class CriterionEvaluation(BaseModel):
    """Per-criterion evaluation detail."""

    id: str = Field(..., description="Criterion identifier.")
    description: str = Field(..., description="Criterion description.")
    met: bool = Field(..., description="Whether the criterion was satisfied.")
    note: Optional[str] = Field(
        default=None, description="Optional explanation / evidence reference."
    )
    status: CriterionStatus | None = Field(
        default=None,
        description="Normalized criterion status: met, not_met, or unknown.",
    )
    supporting_evidence_ids: list[str] = Field(
        default_factory=list,
        description="EvidenceReference ids supporting this rule evaluation.",
    )
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="Evidence still needed for this rule, if any.",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Rule-level reasoning for the criterion status.",
    )
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence for this individual criterion evaluation.",
    )
    review_backend: Optional[str] = Field(
        default=None,
        description="Backend that produced this criterion evaluation.",
    )

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v):
        if v is None or isinstance(v, CriterionStatus):
            return v
        text = str(v).strip().lower().replace("-", "_").replace(" ", "_")
        if text in {"met", "satisfied", "yes", "true"}:
            return CriterionStatus.MET
        if text in {"not_met", "unmet", "missing", "failed", "false", "no"}:
            return CriterionStatus.NOT_MET
        if text in {"unknown", "unclear", "insufficient", "insufficient_information"}:
            return CriterionStatus.UNKNOWN
        return CriterionStatus.UNKNOWN

    @field_validator("supporting_evidence_ids", "missing_evidence", mode="before")
    @classmethod
    def _coerce_str_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(item).strip() for item in v if str(item).strip()]

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _coerce_detail_confidence(cls, v):
        if v is None:
            return 0.0
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    @model_validator(mode="after")
    def _fill_backward_compatible_fields(self):
        if self.status is None:
            self.status = CriterionStatus.MET if self.met else CriterionStatus.NOT_MET
        if not self.reasoning and self.note:
            self.reasoning = self.note
        if self.confidence_score <= 0.0:
            self.confidence_score = 0.85 if self.met else 0.55
        return self


class ReviewResult(BaseModel):
    """Structured result of a clinical guideline review."""

    recommendation: Recommendation = Field(
        ..., description="APPROVE, DENY, or INSUFFICIENT_INFORMATION."
    )
    matched_criteria: list[str] = Field(
        default_factory=list,
        description="Descriptions of criteria that were satisfied.",
    )
    missing_criteria: list[str] = Field(
        default_factory=list,
        description="Descriptions of criteria that were not satisfied.",
    )
    rationale: str = Field(
        default="", description="Human-readable explanation of the decision."
    )
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the recommendation (0.0-1.0).",
    )

    # --- Optional richer detail (back-compatible additions) --- #
    guideline_id: Optional[str] = Field(
        default=None, description="Guideline used for the review, if any."
    )
    service_name: Optional[str] = Field(
        default=None, description="Service/drug reviewed, if identified."
    )

    # --- Final Milestone: payer / guideline-pack provenance (optional) --- #
    payer_id: Optional[str] = Field(
        default=None, description="Payer whose policy governed this review."
    )
    guideline_pack: Optional[str] = Field(
        default=None, description="Guideline pack id used for this review."
    )
    guideline_version: Optional[str] = Field(
        default=None, description="Version of the guideline/pack used."
    )
    generated_by_ai: bool = Field(
        default=False,
        description="Whether a real AI backend produced this review result.",
    )
    review_backend: Optional[str] = Field(
        default=None, description="Backend that produced the review result."
    )
    review_model: Optional[str] = Field(
        default=None, description="Model/backend identifier for the review result."
    )
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="Specific evidence needed to complete the review.",
    )
    recommended_actions: list[str] = Field(
        default_factory=list,
        description="Recommended next actions for the provider/reviewer.",
    )
    contraindications_found: list[str] = Field(
        default_factory=list,
        description="Contraindications detected that justify denial.",
    )
    criteria_detail: list[CriterionEvaluation] = Field(
        default_factory=list,
        description="Per-criterion evaluation detail.",
    )

    # Optional evidence traceability (Milestone 6/7). Maps a logical key to a
    # list of EvidenceReference ids supporting it. Backward-compatible.
    evidence_refs: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Optional evidence references. Keys include 'matched_criteria', "
            "'missing_criteria', 'denial_rationale', 'recommendation'."
        ),
    )

    # --- Milestone 6/7: evidence traceability (back-compatible additions) --- #
    matched_evidence_ids: list[str] = Field(
        default_factory=list,
        description="EvidenceReference ids supporting matched criteria.",
    )
    missing_evidence_ids: list[str] = Field(
        default_factory=list,
        description="EvidenceReference ids relevant to missing criteria.",
    )
    rationale_evidence_ids: list[str] = Field(
        default_factory=list,
        description="EvidenceReference ids supporting the denial rationale.",
    )
    recommendation_evidence_ids: list[str] = Field(
        default_factory=list,
        description="EvidenceReference ids supporting the recommendation.",
    )
    reasoning_backend: Optional[str] = Field(
        default=None, description="Backend that produced AI reasoning, if any."
    )
    reasoning_model: Optional[str] = Field(
        default=None, description="Model identifier that produced AI reasoning."
    )
    safety_gate: dict = Field(
        default_factory=dict,
        description="Latest safety-gate outcome for this review result.",
    )

    @field_validator("recommendation", mode="before")
    @classmethod
    def _coerce_recommendation(cls, v):
        """Map free-form recommendation text to the enum."""
        if v is None:
            return Recommendation.INSUFFICIENT_INFORMATION
        if isinstance(v, Recommendation):
            return v
        text = str(v).strip().upper().replace(" ", "_").replace("-", "_")
        if text in {"APPROVE", "APPROVED", "APPROVAL"}:
            return Recommendation.APPROVE
        if text in {"DENY", "DENIED", "DENIAL", "REJECT"}:
            return Recommendation.DENY
        if "INSUFFICIENT" in text or text in {"UNKNOWN", "UNCLEAR", "NEED_INFO"}:
            return Recommendation.INSUFFICIENT_INFORMATION
        return Recommendation.INSUFFICIENT_INFORMATION

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

    @field_validator(
        "matched_criteria",
        "missing_criteria",
        "missing_evidence",
        "recommended_actions",
        "contraindications_found",
        "matched_evidence_ids",
        "missing_evidence_ids",
        "rationale_evidence_ids",
        "recommendation_evidence_ids",
        mode="before",
    )
    @classmethod
    def _coerce_str_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        cleaned = []
        for item in v:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                cleaned.append(s)
        return cleaned

    @field_validator("rationale", mode="before")
    @classmethod
    def _coerce_rationale(cls, v):
        if v is None:
            return ""
        return str(v).strip()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_criteria(self) -> int:
        """Total criteria considered (matched + missing)."""
        return len(self.matched_criteria) + len(self.missing_criteria)

    def summary(self) -> str:
        """One-line human-readable summary."""
        rec = self.recommendation.value
        svc = self.service_name or "the requested service"
        return f"{rec} for {svc} ({len(self.matched_criteria)}/{self.total_criteria} criteria met)."
