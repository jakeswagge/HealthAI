"""ReviewService: extraction/review attachment + human-review workflow.

Extracted from ``CaseService`` during the Milestone 12 facade decomposition.
Owns the case's clinical-review lifecycle: attaching the extracted patient
case, attaching the automated review result, assigning a reviewer, and
recording the human-review decision (which drives the final status).

Behavior is identical to the original CaseService methods - a cohesion
extraction only.
"""

from __future__ import annotations

from app.audit.repository import AuditRepository
from app.cases.lifecycle import CaseLifecycle
from app.governance.safety import SafetyGate
from app.models.audit_event import AuditActor, AuditEventType
from app.models.case_record import (
    CaseRecord,
    CaseStatus,
    HumanDecision,
    HumanReviewDecision,
)
from app.models.governance import GovernanceSettings
from app.models.patient_case import PatientCase
from app.models.review_result import ReviewResult


class ReviewService:
    """Attach extraction/review artifacts and run the human-review workflow."""

    def __init__(
        self,
        lifecycle: CaseLifecycle,
        audit: AuditRepository,
        settings_provider=None,
    ) -> None:
        self.lifecycle = lifecycle
        self.audit = audit
        self.settings_provider = settings_provider

    def _settings(self) -> GovernanceSettings:
        if self.settings_provider is None:
            return GovernanceSettings()
        return self.settings_provider()

    def attach_extraction(
        self, case_id: str, patient_case: PatientCase
    ) -> CaseRecord:
        """Attach extraction output and move to EXTRACTED."""
        record = self.lifecycle.require(case_id)
        gate = SafetyGate(self._settings()).extraction(patient_case)
        patient_case.safety_gate = gate.model_dump(mode="json")
        record.patient_case = patient_case
        self.lifecycle.set_status(record, CaseStatus.EXTRACTED)
        self.audit.log(
            case_id,
            AuditEventType.EXTRACTION_COMPLETED,
            details=(
                f"Extracted case (confidence {patient_case.confidence_score:.2f}; "
                f"safety={gate.status.value})."
            ),
        )
        return self.lifecycle.save(record)

    def attach_review(self, case_id: str, review: ReviewResult) -> CaseRecord:
        """Attach review output and move to REVIEWED."""
        record = self.lifecycle.require(case_id)
        gate = SafetyGate(self._settings()).review(review)
        review.safety_gate = gate.model_dump(mode="json")
        record.review_result = review
        if record.status is not CaseStatus.PENDING_HUMAN_REVIEW:
            self.lifecycle.set_status(record, CaseStatus.REVIEWED)
        self.audit.log(
            case_id,
            AuditEventType.REVIEW_COMPLETED,
            details=(
                f"Review completed: {review.recommendation.value}; "
                f"safety={gate.status.value}."
            ),
        )
        if gate.requires_human_review:
            if record.status is CaseStatus.REVIEWED:
                self.lifecycle.set_status(
                    record,
                    CaseStatus.APPEAL_GENERATED,
                    log_details="Safety gate routed review to human-review queue.",
                )
            if record.status is CaseStatus.APPEAL_GENERATED:
                self.lifecycle.set_status(record, CaseStatus.PENDING_HUMAN_REVIEW)
        return self.lifecycle.save(record)

    def assign_reviewer(self, case_id: str, reviewer_name: str) -> CaseRecord:
        """Assign a human reviewer to a case."""
        record = self.lifecycle.require(case_id)
        record.assigned_reviewer = reviewer_name
        self.audit.log(
            case_id,
            AuditEventType.STATUS_CHANGED,
            details=f"Assigned reviewer: {reviewer_name}.",
            actor=AuditActor.USER,
        )
        return self.lifecycle.save(record)

    def record_human_review(
        self,
        case_id: str,
        reviewer_name: str,
        decision: HumanDecision | str,
        comments: str = "",
    ) -> CaseRecord:
        """Record a human-review decision and update status accordingly."""
        if not comments or not comments.strip():
            raise ValueError("Reviewer comments are required for human review.")
        record = self.lifecycle.require(case_id)
        review_decision = HumanReviewDecision(
            reviewer_name=reviewer_name,
            decision=decision,
            comments=comments,
        )
        record.review_decisions.append(review_decision)
        record.assigned_reviewer = reviewer_name
        if comments:
            record.review_notes = comments

        decision_enum = review_decision.decision
        if decision_enum is HumanDecision.APPROVE:
            self.lifecycle.set_status(
                record,
                CaseStatus.APPROVED_FOR_EXPORT,
                actor=AuditActor.USER,
                log_details=f"Approved for export by {reviewer_name}.",
            )
        elif decision_enum is HumanDecision.REJECT:
            self.lifecycle.set_status(
                record,
                CaseStatus.REJECTED,
                actor=AuditActor.USER,
                log_details=f"Rejected by {reviewer_name}.",
            )
        else:  # REQUEST_CHANGES
            self.lifecycle.set_status(
                record,
                CaseStatus.APPEAL_GENERATED,
                actor=AuditActor.USER,
                log_details=f"Changes requested by {reviewer_name}.",
            )
            # Return to the review queue after changes are requested.
            self.lifecycle.set_status(record, CaseStatus.PENDING_HUMAN_REVIEW)

        self.audit.log(
            case_id,
            AuditEventType.HUMAN_REVIEW_COMPLETED,
            details=f"{reviewer_name}: {decision_enum.value}. {comments}".strip(),
            actor=AuditActor.USER,
        )
        return self.lifecycle.save(record)
