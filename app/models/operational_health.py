"""Pydantic model for the operational health report (Final Milestone).

An :class:`OperationalHealthReport` aggregates failure/degradation signals from
the local audit trail and governance/quality stores so operators can see system
health without any external observability platform. Everything is derived on
demand from local SQLite.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationalHealthReport(BaseModel):
    """Local operational diagnostics snapshot."""

    generated_at: str = Field(default_factory=_utc_now_iso)
    total_cases: int = 0
    total_documents: int = 0

    # Failure / degradation counts.
    ocr_failures: int = Field(default=0, description="OCR unavailable/empty pages.")
    extraction_failures: int = Field(default=0)
    review_failures: int = Field(default=0)
    appeal_failures: int = Field(default=0)
    claude_fallbacks: int = Field(
        default=0, description="Times AI degraded to the deterministic engine."
    )
    governance_violations: int = Field(default=0)
    conflicts_detected: int = Field(default=0)

    # Derived rates.
    claude_fallback_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    conflict_frequency: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Cases with >=1 conflict / cases."
    )

    # Human-readable health signals (e.g. warnings worth surfacing).
    warnings: list[str] = Field(default_factory=list)

    @property
    def total_failures(self) -> int:
        return (
            self.ocr_failures
            + self.extraction_failures
            + self.review_failures
            + self.appeal_failures
        )

    @property
    def is_healthy(self) -> bool:
        """A coarse health flag: no hard failures and no governance violations."""
        return self.total_failures == 0 and self.governance_violations == 0

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "total_cases": self.total_cases,
            "total_documents": self.total_documents,
            "ocr_failures": self.ocr_failures,
            "extraction_failures": self.extraction_failures,
            "review_failures": self.review_failures,
            "appeal_failures": self.appeal_failures,
            "claude_fallbacks": self.claude_fallbacks,
            "governance_violations": self.governance_violations,
            "conflicts_detected": self.conflicts_detected,
            "claude_fallback_rate": self.claude_fallback_rate,
            "conflict_frequency": self.conflict_frequency,
            "total_failures": self.total_failures,
            "is_healthy": self.is_healthy,
            "warnings": list(self.warnings),
        }
