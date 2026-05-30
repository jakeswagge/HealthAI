"""Pydantic model for a reviewer's decision on a single evidence reference.

Reviewers can APPROVE, REJECT, or FLAG individual pieces of evidence. Only
approved (and not-rejected) evidence is used by the review/appeal workflows when
the reviewer has begun validating a case. Decisions are append-only and audited.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_decision_id() -> str:
    return f"EVD-{uuid.uuid4().hex[:12].upper()}"


class EvidenceDecision(str, Enum):
    """A reviewer's verdict on a piece of evidence."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    FLAG = "FLAG"


class EvidenceReviewDecision(BaseModel):
    """A recorded reviewer decision about one evidence reference."""

    decision_id: str = Field(default_factory=new_decision_id)
    evidence_id: str = Field(...)
    case_id: str = Field(default="")
    reviewer: str = Field(...)
    decision: EvidenceDecision = Field(...)
    comments: str = Field(default="")
    timestamp: str = Field(default_factory=_utc_now_iso)

    @field_validator("decision", mode="before")
    @classmethod
    def _coerce_decision(cls, v):
        if isinstance(v, EvidenceDecision):
            return v
        text = str(v).strip().upper()
        if text in {"APPROVE", "APPROVED"}:
            return EvidenceDecision.APPROVE
        if text in {"REJECT", "REJECTED"}:
            return EvidenceDecision.REJECT
        if text in {"FLAG", "FLAGGED"}:
            return EvidenceDecision.FLAG
        raise ValueError(f"Unknown evidence decision: {v!r}")

    @field_validator("comments", mode="before")
    @classmethod
    def _coerce_comments(cls, v):
        return "" if v is None else str(v)
