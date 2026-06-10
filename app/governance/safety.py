"""Central pilot safety gate."""

from __future__ import annotations

from app.models.appeal_letter import AppealLetter
from app.models.case_record import CaseRecord, HumanDecision
from app.models.governance import GovernanceComplianceReport, GovernanceSettings
from app.models.patient_case import PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.models.safety import SafetyGateDecision, SafetyGateStatus


class SafetyGate:
    """Apply pilot safety policy to workflow artifacts."""

    def __init__(self, settings: GovernanceSettings | None = None) -> None:
        self.settings = settings or GovernanceSettings()

    def extraction(self, case: PatientCase) -> SafetyGateDecision:
        reasons: list[str] = []
        if case.confidence_score < self.settings.confidence_threshold:
            reasons.append(
                f"Extraction confidence {case.confidence_score:.2f} below "
                f"threshold {self.settings.confidence_threshold:.2f}."
            )
        return self._human_or_pass(
            reasons,
            confidence=case.confidence_score,
        )

    def review(self, review: ReviewResult) -> SafetyGateDecision:
        reasons: list[str] = []
        if review.confidence_score < self.settings.confidence_threshold:
            reasons.append(
                f"Review confidence {review.confidence_score:.2f} below "
                f"threshold {self.settings.confidence_threshold:.2f}."
            )
        if (
            self.settings.block_autonomous_denials
            and review.recommendation is Recommendation.DENY
        ):
            reasons.append("Denial recommendation requires human sign-off.")
        return self._human_or_pass(
            reasons,
            confidence=review.confidence_score,
        )

    def appeal(self, appeal: AppealLetter) -> SafetyGateDecision:
        reasons: list[str] = []
        if appeal.confidence_score < self.settings.confidence_threshold:
            reasons.append(
                f"Appeal confidence {appeal.confidence_score:.2f} below "
                f"threshold {self.settings.confidence_threshold:.2f}."
            )
        if (
            self.settings.require_verified_appeal_claims
            and not appeal.verification.passed
        ):
            reasons.append("Appeal claims have not passed evidence verification.")
        return self._human_or_pass(
            reasons,
            confidence=appeal.confidence_score,
        )

    def export(
        self,
        record: CaseRecord,
        compliance: GovernanceComplianceReport,
    ) -> SafetyGateDecision:
        reasons: list[str] = []
        latest = record.latest_decision()
        if (
            self.settings.require_human_review_before_export
            and latest is None
        ):
            reasons.append("Human review is required before export.")
        if (
            self.settings.block_autonomous_denials
            and record.review_result is not None
            and record.review_result.recommendation is Recommendation.DENY
            and not (
                latest is not None
                and latest.decision in {HumanDecision.APPROVE, HumanDecision.REJECT}
            )
        ):
            reasons.append("Denial recommendation has no human sign-off.")
        if not compliance.is_compliant:
            reasons.extend(
                f"{v.code}: {v.description}" for v in compliance.violations
            )
        if (
            self.settings.require_verified_appeal_claims
            and record.appeal_letter is not None
            and not record.appeal_letter.verification.passed
        ):
            reasons.append("Appeal verification is incomplete or failed.")
        status = SafetyGateStatus.BLOCKED if reasons else SafetyGateStatus.PASS
        return SafetyGateDecision(
            status=status,
            reasons=reasons,
            threshold=self.settings.confidence_threshold,
        )

    def _human_or_pass(
        self,
        reasons: list[str],
        *,
        confidence: float | None,
    ) -> SafetyGateDecision:
        status = (
            SafetyGateStatus.HUMAN_REVIEW_REQUIRED
            if reasons
            else SafetyGateStatus.PASS
        )
        return SafetyGateDecision(
            status=status,
            reasons=reasons,
            confidence_score=confidence,
            threshold=self.settings.confidence_threshold,
        )
