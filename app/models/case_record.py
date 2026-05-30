"""Pydantic models for case management and human review.

A :class:`CaseRecord` is the workflow envelope that tracks a single prior-
authorization case as it moves from upload through extraction, review, appeal
generation, human review, and export. It composes the artifacts produced by the
earlier milestones (``PatientCase``, ``ReviewResult``, ``AppealLetter``) and
adds workflow state.

These models are persistence-agnostic; the SQLite repository layer
(``app/cases/repository.py``) serializes them to/from JSON columns.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.appeal_letter import AppealLetter
from app.models.patient_case import PatientCase
from app.models.review_result import ReviewResult


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaseStatus(str, Enum):
    """Lifecycle status of a case."""

    NEW = "NEW"
    EXTRACTED = "EXTRACTED"
    REVIEWED = "REVIEWED"
    APPEAL_GENERATED = "APPEAL_GENERATED"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    APPROVED_FOR_EXPORT = "APPROVED_FOR_EXPORT"
    REJECTED = "REJECTED"


class HumanDecision(str, Enum):
    """The decision a human reviewer can record."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class HumanReviewDecision(BaseModel):
    """A recorded human-review decision on a case."""

    reviewer_name: str = Field(..., description="Name of the human reviewer.")
    decision: HumanDecision = Field(..., description="The reviewer's decision.")
    comments: str = Field(default="", description="Free-text reviewer comments.")
    timestamp: str = Field(
        default_factory=_utc_now_iso,
        description="ISO-8601 timestamp the decision was recorded.",
    )

    @field_validator("decision", mode="before")
    @classmethod
    def _coerce_decision(cls, v):
        if isinstance(v, HumanDecision):
            return v
        text = str(v).strip().upper().replace(" ", "_").replace("-", "_")
        if text in {"APPROVE", "APPROVED"}:
            return HumanDecision.APPROVE
        if text in {"REJECT", "REJECTED"}:
            return HumanDecision.REJECT
        if "CHANGE" in text:
            return HumanDecision.REQUEST_CHANGES
        raise ValueError(f"Unknown human decision: {v!r}")

    @field_validator("comments", mode="before")
    @classmethod
    def _coerce_comments(cls, v):
        return "" if v is None else str(v)


class CaseRecord(BaseModel):
    """Workflow record tracking a single prior-authorization case."""

    case_id: str = Field(..., description="Unique case identifier.")
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)
    status: CaseStatus = Field(default=CaseStatus.NEW)

    # Source / display helpers.
    source_filename: Optional[str] = Field(
        default=None, description="Original uploaded filename, if any."
    )

    # Composed artifacts from earlier milestones.
    patient_case: Optional[PatientCase] = Field(default=None)
    review_result: Optional[ReviewResult] = Field(default=None)
    appeal_letter: Optional[AppealLetter] = Field(default=None)

    # Human-review fields.
    assigned_reviewer: Optional[str] = Field(default=None)
    review_notes: str = Field(default="")
    review_decisions: list[HumanReviewDecision] = Field(default_factory=list)

    # Lightweight timing for metrics (seconds), optional.
    processing_seconds: Optional[float] = Field(default=None)

    @field_validator("review_notes", mode="before")
    @classmethod
    def _coerce_notes(cls, v):
        return "" if v is None else str(v)

    # ------------------------------------------------------------------ #
    # Convenience accessors
    # ------------------------------------------------------------------ #
    def display_name(self) -> str:
        """A short human-readable label for the case."""
        if self.patient_case and self.patient_case.patient_name:
            who = self.patient_case.patient_name
        else:
            who = "Unknown patient"
        svc = (
            self.patient_case.requested_service
            if self.patient_case and self.patient_case.requested_service
            else "unspecified service"
        )
        return f"{self.case_id} — {who} ({svc})"

    def latest_decision(self) -> Optional[HumanReviewDecision]:
        return self.review_decisions[-1] if self.review_decisions else None

    def touch(self) -> None:
        """Update the ``updated_at`` timestamp to now."""
        self.updated_at = _utc_now_iso()
