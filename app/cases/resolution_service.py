"""ResolutionService: human conflict resolution + reviewer feedback (M8).

Extracted from ``CaseService`` during the Milestone 12 facade decomposition.
Owns the human-authority concern: recording conflict resolutions (which become
authoritative facts) and capturing structured reviewer feedback. Behavior is
identical to the original CaseService methods - a cohesion extraction only.
"""

from __future__ import annotations

from typing import Optional

from app.audit.repository import AuditRepository
from app.cases.lifecycle import CaseLifecycle
from app.feedback.repository import ReviewerFeedbackRepository
from app.resolution.engine import ConflictResolutionEngine
from app.resolution.repository import (
    AuthoritativeFactRepository,
    ConflictResolutionRepository,
)
from app.models.audit_event import AuditActor, AuditEventType
from app.models.conflict_resolution import AuthoritativeFact, ConflictResolution
from app.models.patient_case import PatientCase
from app.models.reviewer_feedback import (
    FeedbackTarget,
    FeedbackVerdict,
    ReviewerFeedback,
)


class ResolutionService:
    """Resolve conflicts into authoritative facts + record reviewer feedback."""

    def __init__(
        self,
        lifecycle: CaseLifecycle,
        resolution_engine: ConflictResolutionEngine,
        resolutions: ConflictResolutionRepository,
        authoritative_facts: AuthoritativeFactRepository,
        feedback: ReviewerFeedbackRepository,
        audit: AuditRepository,
    ) -> None:
        self.lifecycle = lifecycle
        self.resolution_engine = resolution_engine
        self.resolutions = resolutions
        self.authoritative_facts = authoritative_facts
        self.feedback = feedback
        self.audit = audit

    def resolve_conflict(
        self,
        case_id: str,
        conflict_id: str,
        fact_type: str,
        chosen_value: str,
        rejected_values: list[str],
        reviewer_name: str,
        justification: str = "",
        source_document: str | None = None,
        source_page: int | None = None,
    ) -> tuple[ConflictResolution, AuthoritativeFact]:
        """Record a human conflict resolution and update the case.

        The reviewer's choice becomes the authoritative value; rejected values
        are preserved; the patient case on the record is updated from the
        authoritative facts; audit events are recorded by the engine.
        """
        record = self.lifecycle.require(case_id)
        resolution, fact = self.resolution_engine.resolve(
            case_id=case_id,
            conflict_id=conflict_id,
            fact_type=fact_type,
            chosen_value=chosen_value,
            rejected_values=rejected_values,
            reviewer_name=reviewer_name,
            justification=justification,
            source_document=source_document,
            source_page=source_page,
        )
        # Reflect authoritative facts on the stored patient case so review +
        # appeal use the human-chosen values.
        if record.patient_case is not None:
            record.patient_case = self.resolution_engine.apply_to_case(
                record.patient_case, case_id
            )
            self.lifecycle.save(record)
        return resolution, fact

    def list_resolutions(self, case_id: str) -> list[ConflictResolution]:
        return self.resolutions.for_case(case_id)

    def list_authoritative_facts(self, case_id: str) -> list[AuthoritativeFact]:
        return self.authoritative_facts.for_case(case_id)

    def authoritative_patient_case(self, case_id: str) -> Optional[PatientCase]:
        """Return the record's patient case with authoritative facts applied."""
        record = self.lifecycle.require(case_id)
        if record.patient_case is None:
            return None
        return self.resolution_engine.apply_to_case(record.patient_case, case_id)

    def record_reviewer_feedback(
        self,
        case_id: str,
        reviewer: str,
        target_type: FeedbackTarget | str,
        feedback: FeedbackVerdict | str,
        target_id: str | None = None,
        comments: str = "",
    ) -> ReviewerFeedback:
        """Record structured reviewer feedback and audit it."""
        self.lifecycle.require(case_id)
        fb = ReviewerFeedback(
            case_id=case_id,
            reviewer=reviewer,
            target_type=target_type,
            target_id=target_id,
            feedback=feedback,
            comments=comments,
        )
        self.feedback.add(fb)
        self.audit.log(
            case_id,
            AuditEventType.REVIEWER_FEEDBACK_RECORDED,
            details=(
                f"{reviewer} rated {fb.target_type.value} as {fb.feedback.value}. "
                f"{comments}"
            ).strip(),
            actor=AuditActor.USER,
        )
        return fb

    def list_feedback(self, case_id: str) -> list[ReviewerFeedback]:
        return self.feedback.for_case(case_id)
