"""Pilot safety-gate and verification models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


DEFAULT_CONFIDENCE_THRESHOLD = 0.85


class SafetyGateStatus(str, Enum):
    """Outcome of applying pilot safety policy to an artifact."""

    PASS = "PASS"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


class SafetyGateDecision(BaseModel):
    """Structured result from a safety-gate check."""

    status: SafetyGateStatus = SafetyGateStatus.PASS
    reasons: list[str] = Field(default_factory=list)
    confidence_score: float | None = None
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD

    @property
    def passed(self) -> bool:
        return self.status is SafetyGateStatus.PASS

    @property
    def requires_human_review(self) -> bool:
        return self.status is SafetyGateStatus.HUMAN_REVIEW_REQUIRED

    @property
    def blocked(self) -> bool:
        return self.status is SafetyGateStatus.BLOCKED

    @field_validator("reasons", mode="before")
    @classmethod
    def _coerce_reasons(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(item).strip() for item in v if str(item).strip()]

    @field_validator("threshold", mode="before")
    @classmethod
    def _coerce_threshold(cls, v):
        if v is None:
            return DEFAULT_CONFIDENCE_THRESHOLD
        try:
            f = float(v)
        except (TypeError, ValueError):
            return DEFAULT_CONFIDENCE_THRESHOLD
        return max(0.0, min(1.0, f))


class AppealVerificationStatus(str, Enum):
    """Verification outcome for generated appeal text."""

    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"
    CORRECTED = "CORRECTED"


class AppealVerificationResult(BaseModel):
    """Result of checking appeal claims against source-backed evidence."""

    status: AppealVerificationStatus = AppealVerificationStatus.NOT_RUN
    unsupported_claims: list[str] = Field(default_factory=list)
    corrected_text: str | None = None
    verifier_backend: str | None = None
    verifier_model: str | None = None
    cited_evidence_ids: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status in {
            AppealVerificationStatus.PASSED,
            AppealVerificationStatus.CORRECTED,
        }

    @field_validator("unsupported_claims", "cited_evidence_ids", mode="before")
    @classmethod
    def _coerce_str_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(item).strip() for item in v if str(item).strip()]
