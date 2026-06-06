"""Shared case-lifecycle primitives used by CaseService and its sub-services.

Milestone 12 decomposed the ~700-line ``CaseService`` god object into a facade
plus cohesive sub-services (evidence, review, appeal, governance, analytics,
export, ingestion, resolution). Several of those sub-services need to look up a
case, apply a validated status transition, and persist the record. Rather than
duplicate that logic, it lives here in one place so the status-transition rules
(``can_transition``) and audit logging stay consistent.

Behavior is identical to the original private ``_require`` / ``_set_status``
helpers - this is a de-duplication extraction, not a logic change.
"""

from __future__ import annotations

from typing import Optional

from app.audit.repository import AuditRepository
from app.cases.repository import CaseRepository
from app.cases.transitions import InvalidTransitionError, can_transition
from app.models.audit_event import AuditActor, AuditEventType
from app.models.case_record import CaseRecord, CaseStatus


class CaseLifecycle:
    """Case lookup + validated status transitions + persistence + audit.

    Wraps the shared :class:`CaseRepository` and :class:`AuditRepository` so the
    facade and every sub-service drive case status the same way.
    """

    def __init__(self, cases: CaseRepository, audit: AuditRepository) -> None:
        self.cases = cases
        self.audit = audit

    def require(self, case_id: str) -> CaseRecord:
        record = self.cases.get(case_id)
        if record is None:
            raise KeyError(f"No such case: {case_id}")
        return record

    def save(self, record: CaseRecord) -> CaseRecord:
        return self.cases.save(record)

    def set_status(
        self,
        record: CaseRecord,
        target: CaseStatus,
        actor: AuditActor = AuditActor.SYSTEM,
        log_details: Optional[str] = None,
    ) -> None:
        """Validate + apply a status transition, recording an audit event."""
        if not can_transition(record.status, target):
            raise InvalidTransitionError(
                f"Cannot move case {record.case_id} from {record.status.value} "
                f"to {target.value}."
            )
        if record.status != target:
            previous = record.status
            record.status = target
            self.audit.log(
                record.case_id,
                AuditEventType.STATUS_CHANGED,
                details=(log_details or f"Status changed: {previous.value} -> {target.value}"),
                actor=actor,
            )
