"""AppealService: attach generated appeals + the export-mark hook.

Extracted from ``CaseService`` during the Milestone 12 facade decomposition.
Owns the appeal-attachment transition (which automatically queues the case for
human review) and the ``mark_exported`` audit hook used after an export package
is produced.

Behavior is identical to the original CaseService methods - a cohesion
extraction only.
"""

from __future__ import annotations

from app.audit.repository import AuditRepository
from app.cases.lifecycle import CaseLifecycle
from app.models.appeal_letter import AppealLetter
from app.models.audit_event import AuditActor, AuditEventType
from app.models.case_record import CaseRecord, CaseStatus


class AppealService:
    """Attach appeal letters and record export generation."""

    def __init__(self, lifecycle: CaseLifecycle, audit: AuditRepository) -> None:
        self.lifecycle = lifecycle
        self.audit = audit

    def attach_appeal(self, case_id: str, appeal: AppealLetter) -> CaseRecord:
        """Attach appeal output, move to APPEAL_GENERATED then PENDING review."""
        record = self.lifecycle.require(case_id)
        record.appeal_letter = appeal
        self.lifecycle.set_status(record, CaseStatus.APPEAL_GENERATED)
        self.audit.log(
            case_id,
            AuditEventType.APPEAL_GENERATED,
            details=f"Appeal generated: {appeal.appeal_id}.",
        )
        # Appeals automatically enter the human-review queue.
        self.lifecycle.set_status(record, CaseStatus.PENDING_HUMAN_REVIEW)
        return self.lifecycle.save(record)

    def mark_exported(self, case_id: str) -> CaseRecord:
        """Record that a case's export package was generated."""
        record = self.lifecycle.require(case_id)
        self.audit.log(
            case_id,
            AuditEventType.CASE_EXPORTED,
            details="Export package generated.",
            actor=AuditActor.USER,
        )
        return self.lifecycle.save(record)
