"""Computed human-review workqueue buckets."""

from __future__ import annotations

from enum import Enum

from app.models.case_record import CaseRecord, CaseStatus
from app.models.safety import AppealVerificationStatus, SafetyGateStatus


class WorkqueueBucket(str, Enum):
    EXTRACTION_REVIEW = "Needs extraction review"
    UNCERTAIN_REVIEW = "Uncertain review"
    CONFLICT_RESOLUTION = "Conflict resolution"
    APPEAL_VERIFICATION_FAILED = "Appeal verification failed"
    READY_FOR_SIGN_OFF = "Ready for sign-off"
    EXPORT_BLOCKED = "Export blocked"


def bucket_for_case(record: CaseRecord) -> WorkqueueBucket | None:
    """Return the highest-priority workqueue bucket for a case."""
    if record.status is not CaseStatus.PENDING_HUMAN_REVIEW:
        return None

    pc_gate = (record.patient_case.safety_gate if record.patient_case else {}) or {}
    if pc_gate.get("status") == SafetyGateStatus.HUMAN_REVIEW_REQUIRED.value:
        return WorkqueueBucket.EXTRACTION_REVIEW

    if record.appeal_letter is not None:
        verification = record.appeal_letter.verification
        if verification.status in {
            AppealVerificationStatus.FAILED,
            AppealVerificationStatus.NOT_RUN,
        }:
            return WorkqueueBucket.APPEAL_VERIFICATION_FAILED

    rr_gate = (record.review_result.safety_gate if record.review_result else {}) or {}
    reasons = " ".join(rr_gate.get("reasons", []))
    if "Denial recommendation" in reasons:
        return WorkqueueBucket.READY_FOR_SIGN_OFF
    if rr_gate.get("status") == SafetyGateStatus.HUMAN_REVIEW_REQUIRED.value:
        return WorkqueueBucket.UNCERTAIN_REVIEW

    return WorkqueueBucket.READY_FOR_SIGN_OFF
