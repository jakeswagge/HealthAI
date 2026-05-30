"""GovernanceComplianceChecker: detect governance-policy violations on a case.

Detects:
- appeals generated with weak evidence,
- unresolved conflicts (when policy requires resolution),
- exports without human review (when policy requires it),
- low-quality evidence usage (below the minimum quality score).

Returns a :class:`GovernanceComplianceReport`. Pure/deterministic; the caller
records the audit event.
"""

from __future__ import annotations

from app.models.conflict_report import ConflictReport
from app.models.evidence_quality import EvidenceQualityAssessment
from app.models.governance import (
    ComplianceViolation,
    EvidenceMode,
    GovernanceComplianceReport,
    GovernanceSettings,
)


class GovernanceComplianceChecker:
    """Check a case against the active governance policy."""

    def check(
        self,
        case_id: str,
        settings: GovernanceSettings,
        *,
        has_appeal: bool,
        has_human_review: bool,
        was_exported: bool,
        quality: list[EvidenceQualityAssessment],
        conflict_report: ConflictReport | None,
        unresolved_conflict_fact_types: list[str] | None = None,
        used_evidence_ids: set[str] | None = None,
    ) -> GovernanceComplianceReport:
        used_evidence_ids = used_evidence_ids or set()
        unresolved = unresolved_conflict_fact_types or []
        violations: list[ComplianceViolation] = []

        quality_by_id = {q.evidence_id: q for q in quality}

        # 1. Appeal generated with weak evidence.
        if has_appeal:
            weak = [q for q in quality if q.is_weak]
            # Only weak evidence that is actually used counts (if we know usage).
            if used_evidence_ids:
                weak = [q for q in weak if q.evidence_id in used_evidence_ids]
            if weak:
                violations.append(
                    ComplianceViolation(
                        code="APPEAL_WITH_WEAK_EVIDENCE",
                        severity="HIGH",
                        description=(
                            f"Appeal generated while {len(weak)} weak evidence "
                            "reference(s) are present."
                        ),
                        evidence_ids=[q.evidence_id for q in weak],
                    )
                )

        # 2. Unresolved conflicts (policy requires resolution).
        if settings.require_conflict_resolution and unresolved:
            violations.append(
                ComplianceViolation(
                    code="UNRESOLVED_CONFLICTS",
                    severity="HIGH",
                    description=(
                        "Unresolved conflicts remain for: "
                        + ", ".join(unresolved)
                    ),
                )
            )

        # 3. Export without human review (policy requires it).
        if (
            settings.require_human_review_before_export
            and was_exported
            and not has_human_review
        ):
            violations.append(
                ComplianceViolation(
                    code="EXPORT_WITHOUT_HUMAN_REVIEW",
                    severity="HIGH",
                    description="Case was exported without a human-review decision.",
                )
            )

        # 4. Low-quality evidence usage (below the minimum threshold).
        if settings.minimum_quality_score > 0.0:
            low = [
                q for q in quality
                if q.overall_score < settings.minimum_quality_score
                and (not used_evidence_ids or q.evidence_id in used_evidence_ids)
            ]
            if low:
                violations.append(
                    ComplianceViolation(
                        code="LOW_QUALITY_EVIDENCE_PRESENT",
                        severity="MEDIUM",
                        description=(
                            f"{len(low)} evidence reference(s) below the minimum "
                            f"quality score {settings.minimum_quality_score:.2f}."
                        ),
                        evidence_ids=[q.evidence_id for q in low],
                    )
                )

        return GovernanceComplianceReport(
            case_id=case_id,
            mode=settings.mode,
            violations=violations,
        )
