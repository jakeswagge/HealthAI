"""ExplainabilityEngine: build governance-aware explanations + lineage.

Given the raw inputs of a case (all evidence, reviewer decisions, quality
assessments) and the governance-filtered :class:`ApprovedEvidenceSet`, this
engine produces:

- a :class:`TraceabilityChain` (every evidence id linked to source document,
  page, reviewer decision, and quality score; flagged included vs. excluded),
- a :class:`ReviewExplanation` (why the review reached its recommendation,
  which evidence was used vs. excluded, governance mode + confidence),
- an :class:`AppealExplanation` (the same, for a generated appeal letter).

Governance contract (VALIDATED mode)
------------------------------------
``evidence_used`` is built exclusively from ``approved_set.included_ids``.
Rejected/excluded evidence is placed in ``evidence_excluded`` with the reason
recorded by the governance engine; it never appears in ``evidence_used`` and so
cannot influence the recommendation, rationale, confidence, or appeal content.

The engine is pure/deterministic and offline; the caller records audit events.
"""

from __future__ import annotations

from app.models.appeal_letter import AppealLetter
from app.models.evidence_quality import EvidenceQualityAssessment
from app.models.evidence_reference import EvidenceReference
from app.models.evidence_review_decision import EvidenceReviewDecision
from app.models.explanation import (
    AppealExplanation,
    EvidenceLineage,
    ReviewExplanation,
    TraceabilityChain,
)
from app.models.governance import ApprovedEvidenceSet, EvidenceMode
from app.models.review_result import Recommendation, ReviewResult


def _value_of(ref: EvidenceReference) -> str:
    return ref.normalized_fact.split(": ", 1)[-1] if ref.normalized_fact else ""


class ExplainabilityEngine:
    """Build review/appeal explanations and the evidence traceability chain."""

    # ------------------------------------------------------------------ #
    # Lineage
    # ------------------------------------------------------------------ #
    def build_lineage(
        self,
        evidence: list[EvidenceReference],
        approved_set: ApprovedEvidenceSet,
        *,
        decisions_by_evidence: dict[str, EvidenceReviewDecision] | None = None,
        quality_by_id: dict[str, EvidenceQualityAssessment] | None = None,
    ) -> list[EvidenceLineage]:
        """Build one :class:`EvidenceLineage` per evidence reference."""
        decisions_by_evidence = decisions_by_evidence or {}
        quality_by_id = quality_by_id or {}
        included_ids = set(approved_set.included_ids)
        exclusion_reason = {
            e.evidence_id: e.reason for e in approved_set.excluded
        }

        links: list[EvidenceLineage] = []
        for ev in evidence:
            decision = decisions_by_evidence.get(ev.evidence_id)
            q = quality_by_id.get(ev.evidence_id)
            included = ev.evidence_id in included_ids
            links.append(
                EvidenceLineage(
                    evidence_id=ev.evidence_id,
                    fact_type=ev.fact_type,
                    value=_value_of(ev),
                    source_document_id=ev.source_document_id,
                    source_filename=ev.source_filename,
                    page_number=ev.page_number,
                    quoted_text=ev.quoted_text,
                    reviewer_decision=(
                        decision.decision.value if decision else "PENDING"
                    ),
                    quality_score=(q.overall_score if q else None),
                    included=included,
                    exclusion_reason=exclusion_reason.get(ev.evidence_id, ""),
                )
            )
        return links

    def build_traceability_chain(
        self,
        case_id: str,
        evidence: list[EvidenceReference],
        approved_set: ApprovedEvidenceSet,
        *,
        decisions_by_evidence: dict[str, EvidenceReviewDecision] | None = None,
        quality_by_id: dict[str, EvidenceQualityAssessment] | None = None,
    ) -> TraceabilityChain:
        """Build the full traceability chain for a case."""
        links = self.build_lineage(
            evidence,
            approved_set,
            decisions_by_evidence=decisions_by_evidence,
            quality_by_id=quality_by_id,
        )
        return TraceabilityChain(
            case_id=case_id,
            governance_mode=approved_set.mode,
            links=links,
        )

    @staticmethod
    def _split(links: list[EvidenceLineage]) -> tuple[list, list]:
        used = [link for link in links if link.included]
        excluded = [link for link in links if not link.included]
        return used, excluded

    # ------------------------------------------------------------------ #
    # Review explanation
    # ------------------------------------------------------------------ #
    def explain_review(
        self,
        case_id: str,
        review: ReviewResult,
        evidence: list[EvidenceReference],
        approved_set: ApprovedEvidenceSet,
        *,
        decisions_by_evidence: dict[str, EvidenceReviewDecision] | None = None,
        quality_by_id: dict[str, EvidenceQualityAssessment] | None = None,
    ) -> ReviewExplanation:
        """Explain a review result, honoring the governance evidence split."""
        links = self.build_lineage(
            evidence,
            approved_set,
            decisions_by_evidence=decisions_by_evidence,
            quality_by_id=quality_by_id,
        )
        used, excluded = self._split(links)

        review_id = f"REV-{case_id}-{review.guideline_id or 'none'}"
        reasoning = self._review_reasoning(review, approved_set, used, excluded)

        return ReviewExplanation(
            review_id=review_id,
            case_id=case_id,
            recommendation=review.recommendation.value,
            governance_mode=approved_set.mode,
            confidence=review.confidence_score,
            evidence_used=used,
            evidence_excluded=excluded,
            reasoning_steps=reasoning,
        )

    @staticmethod
    def _review_reasoning(
        review: ReviewResult,
        approved_set: ApprovedEvidenceSet,
        used: list[EvidenceLineage],
        excluded: list[EvidenceLineage],
    ) -> list[str]:
        steps: list[str] = []
        if approved_set.mode is EvidenceMode.VALIDATED:
            steps.append(
                "Governance mode VALIDATED: only governance-approved evidence "
                "was permitted to influence this review."
            )
        else:
            steps.append(
                "Governance mode DRAFT: all available evidence was considered."
            )
        steps.append(
            f"{len(used)} evidence reference(s) used; "
            f"{len(excluded)} excluded from influence."
        )
        if review.guideline_id:
            steps.append(
                f"Matched guideline {review.guideline_id}"
                + (f" ({review.service_name})." if review.service_name else ".")
            )
        if review.matched_criteria:
            steps.append(
                f"{len(review.matched_criteria)} criterion/criteria supported by "
                "the permitted record: " + "; ".join(review.matched_criteria) + "."
            )
        if review.missing_criteria:
            steps.append(
                f"{len(review.missing_criteria)} criterion/criteria not "
                "established: " + "; ".join(review.missing_criteria) + "."
            )
        if review.contraindications_found:
            steps.append(
                "Contraindication(s): " + "; ".join(review.contraindications_found) + "."
            )
        steps.append(
            f"Recommendation {review.recommendation.value} at "
            f"{review.confidence_score:.0%} confidence."
        )
        if excluded:
            steps.append(
                "Excluded evidence did not contribute to the recommendation, "
                "rationale, or confidence."
            )
        return steps

    # ------------------------------------------------------------------ #
    # Appeal explanation
    # ------------------------------------------------------------------ #
    def explain_appeal(
        self,
        case_id: str,
        appeal: AppealLetter,
        evidence: list[EvidenceReference],
        approved_set: ApprovedEvidenceSet,
        *,
        decisions_by_evidence: dict[str, EvidenceReviewDecision] | None = None,
        quality_by_id: dict[str, EvidenceQualityAssessment] | None = None,
    ) -> AppealExplanation:
        """Explain a generated appeal, honoring the governance evidence split."""
        links = self.build_lineage(
            evidence,
            approved_set,
            decisions_by_evidence=decisions_by_evidence,
            quality_by_id=quality_by_id,
        )
        used, excluded = self._split(links)

        reasoning = self._appeal_reasoning(appeal, approved_set, used, excluded)

        return AppealExplanation(
            appeal_id=appeal.appeal_id,
            case_id=case_id,
            governance_mode=approved_set.mode,
            confidence=appeal.confidence_score,
            evidence_used=used,
            evidence_excluded=excluded,
            guideline_support=list(appeal.guideline_support),
            missing_evidence=list(appeal.missing_information),
            reasoning_steps=reasoning,
        )

    @staticmethod
    def _appeal_reasoning(
        appeal: AppealLetter,
        approved_set: ApprovedEvidenceSet,
        used: list[EvidenceLineage],
        excluded: list[EvidenceLineage],
    ) -> list[str]:
        steps: list[str] = []
        if approved_set.mode is EvidenceMode.VALIDATED:
            steps.append(
                "Governance mode VALIDATED: the appeal was drafted using only "
                "governance-approved evidence."
            )
        else:
            steps.append(
                "Governance mode DRAFT: the appeal could draw on all evidence."
            )
        steps.append(
            f"{len(used)} evidence reference(s) used; "
            f"{len(excluded)} excluded from the appeal."
        )
        if appeal.guideline_support:
            steps.append(
                f"{len(appeal.guideline_support)} guideline-support point(s) cited."
            )
        if appeal.missing_information:
            steps.append(
                f"{len(appeal.missing_information)} evidence gap(s) disclosed "
                "honestly rather than asserted."
            )
        steps.append(
            f"Appeal confidence {appeal.confidence_score:.0%}."
        )
        if excluded:
            steps.append(
                "Excluded evidence did not contribute to any appeal statement."
            )
        return steps
