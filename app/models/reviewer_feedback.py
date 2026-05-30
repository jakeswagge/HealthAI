"""Pydantic model for structured reviewer feedback.

Captures a reviewer's assessment of an extraction, review, appeal, or assembly
result. This is structured learning data only - it is stored and exportable but
never used to retrain a model (no ML in this milestone).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_feedback_id() -> str:
    return f"FB-{uuid.uuid4().hex[:12].upper()}"


class FeedbackTarget(str, Enum):
    """Which pipeline stage the feedback is about."""

    EXTRACTION = "EXTRACTION"
    REVIEW = "REVIEW"
    APPEAL = "APPEAL"
    ASSEMBLY = "ASSEMBLY"


class FeedbackVerdict(str, Enum):
    """The reviewer's verdict on the target."""

    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    PARTIAL = "PARTIAL"


class ReviewerFeedback(BaseModel):
    """A single piece of structured reviewer feedback."""

    feedback_id: str = Field(default_factory=new_feedback_id)
    case_id: str = Field(...)
    reviewer: str = Field(...)
    target_type: FeedbackTarget = Field(...)
    target_id: Optional[str] = Field(
        default=None, description="Identifier of the artifact being assessed."
    )
    feedback: FeedbackVerdict = Field(...)
    comments: str = Field(default="")
    timestamp: str = Field(default_factory=_utc_now_iso)

    @field_validator("target_type", mode="before")
    @classmethod
    def _coerce_target(cls, v):
        if isinstance(v, FeedbackTarget):
            return v
        return FeedbackTarget(str(v).strip().upper())

    @field_validator("feedback", mode="before")
    @classmethod
    def _coerce_verdict(cls, v):
        if isinstance(v, FeedbackVerdict):
            return v
        return FeedbackVerdict(str(v).strip().upper())

    @field_validator("comments", mode="before")
    @classmethod
    def _coerce_comments(cls, v):
        return "" if v is None else str(v)
