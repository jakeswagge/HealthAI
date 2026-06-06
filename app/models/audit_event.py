"""Pydantic model for audit events.

An :class:`AuditEvent` records a significant action taken against a case. Events
are immutable once written and stored in SQLite via ``app/audit/repository.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditEventType(str, Enum):
    """Categories of auditable actions."""

    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    EXTRACTION_COMPLETED = "EXTRACTION_COMPLETED"
    REVIEW_COMPLETED = "REVIEW_COMPLETED"
    APPEAL_GENERATED = "APPEAL_GENERATED"
    HUMAN_REVIEW_COMPLETED = "HUMAN_REVIEW_COMPLETED"
    CASE_EXPORTED = "CASE_EXPORTED"
    # Generic catch-all for status changes / other notable actions.
    STATUS_CHANGED = "STATUS_CHANGED"
    CASE_CREATED = "CASE_CREATED"
    # Milestone 6/7: multi-document assembly + evidence.
    CASE_DOCUMENT_ADDED = "CASE_DOCUMENT_ADDED"
    CASE_ASSEMBLED = "CASE_ASSEMBLED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    # Milestone 8: human conflict resolution + reviewer feedback.
    CONFLICT_RESOLVED = "CONFLICT_RESOLVED"
    AUTHORITATIVE_FACT_UPDATED = "AUTHORITATIVE_FACT_UPDATED"
    REVIEWER_FEEDBACK_RECORDED = "REVIEWER_FEEDBACK_RECORDED"
    # Milestone 10: Claude evidence extraction + quality + workbench.
    EVIDENCE_EXTRACTED_AI = "EVIDENCE_EXTRACTED_AI"
    EVIDENCE_QUALITY_SCORED = "EVIDENCE_QUALITY_SCORED"
    EVIDENCE_REVIEW_DECISION = "EVIDENCE_REVIEW_DECISION"
    # Milestone 11: governance + validated evidence mode.
    GOVERNANCE_SETTINGS_UPDATED = "GOVERNANCE_SETTINGS_UPDATED"
    VALIDATED_EVIDENCE_APPLIED = "VALIDATED_EVIDENCE_APPLIED"
    COMPLIANCE_CHECK_RUN = "COMPLIANCE_CHECK_RUN"
    # Milestone 13: governance-enforced reviews/appeals + explainability.
    REVIEW_EXPLANATION_GENERATED = "REVIEW_EXPLANATION_GENERATED"
    APPEAL_EXPLANATION_GENERATED = "APPEAL_EXPLANATION_GENERATED"


class AuditActor(str, Enum):
    """Who/what initiated the action."""

    SYSTEM = "SYSTEM"
    USER = "USER"


class AuditEvent(BaseModel):
    """A single immutable audit-trail entry."""

    event_id: str = Field(default_factory=lambda: f"EVT-{uuid.uuid4().hex[:12].upper()}")
    timestamp: str = Field(default_factory=_utc_now_iso)
    case_id: str = Field(..., description="Case this event belongs to.")
    event_type: AuditEventType = Field(..., description="Type of action.")
    actor: AuditActor = Field(default=AuditActor.SYSTEM)
    details: str = Field(default="", description="Human-readable details.")

    @field_validator("event_type", mode="before")
    @classmethod
    def _coerce_type(cls, v):
        if isinstance(v, AuditEventType):
            return v
        return AuditEventType(str(v).strip().upper())

    @field_validator("actor", mode="before")
    @classmethod
    def _coerce_actor(cls, v):
        if isinstance(v, AuditActor):
            return v
        if v is None:
            return AuditActor.SYSTEM
        return AuditActor(str(v).strip().upper())

    @field_validator("details", mode="before")
    @classmethod
    def _coerce_details(cls, v):
        return "" if v is None else str(v)
