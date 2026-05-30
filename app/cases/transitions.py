"""Case status-transition rules.

Defines the legal lifecycle transitions for a :class:`CaseStatus`. Keeping this
explicit (rather than letting any status move to any other) makes the workflow
auditable and lets tests assert that illegal jumps are rejected.
"""

from __future__ import annotations

from app.models.case_record import CaseStatus

# Map of status -> set of statuses it may transition to.
ALLOWED_TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.NEW: {CaseStatus.EXTRACTED, CaseStatus.REJECTED},
    CaseStatus.EXTRACTED: {CaseStatus.REVIEWED, CaseStatus.REJECTED},
    CaseStatus.REVIEWED: {CaseStatus.APPEAL_GENERATED, CaseStatus.REJECTED},
    CaseStatus.APPEAL_GENERATED: {
        CaseStatus.PENDING_HUMAN_REVIEW,
        CaseStatus.REJECTED,
    },
    CaseStatus.PENDING_HUMAN_REVIEW: {
        CaseStatus.APPROVED_FOR_EXPORT,
        CaseStatus.REJECTED,
        # REQUEST_CHANGES sends the case back for a fresh appeal.
        CaseStatus.APPEAL_GENERATED,
    },
    # Terminal-ish states: export-approved can still be rejected; rejected can
    # be reopened for another appeal attempt.
    CaseStatus.APPROVED_FOR_EXPORT: {CaseStatus.REJECTED},
    CaseStatus.REJECTED: {CaseStatus.APPEAL_GENERATED},
}


class InvalidTransitionError(Exception):
    """Raised when an illegal status transition is attempted."""


def can_transition(current: CaseStatus, target: CaseStatus) -> bool:
    """Return True if moving from ``current`` to ``target`` is allowed.

    A no-op transition (current == target) is always allowed (idempotent).
    """
    if current == target:
        return True
    return target in ALLOWED_TRANSITIONS.get(current, set())
