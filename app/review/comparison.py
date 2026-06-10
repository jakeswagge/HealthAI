"""Comparison helpers for deterministic vs. AI clinical review outputs."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.review_result import CriterionStatus, Recommendation, ReviewResult


@dataclass
class ReviewComparison:
    """Material/non-material differences between local and AI review."""

    deterministic_recommendation: str
    ai_recommendation: str
    material_disagreements: list[str] = field(default_factory=list)
    non_material_differences: list[str] = field(default_factory=list)
    invalid_evidence_ids: list[str] = field(default_factory=list)
    confidence_threshold: float = 0.85

    @property
    def requires_human_review(self) -> bool:
        return bool(self.material_disagreements or self.invalid_evidence_ids)

    def as_dict(self) -> dict:
        return {
            "deterministic_recommendation": self.deterministic_recommendation,
            "ai_recommendation": self.ai_recommendation,
            "material_disagreements": list(self.material_disagreements),
            "non_material_differences": list(self.non_material_differences),
            "invalid_evidence_ids": list(self.invalid_evidence_ids),
            "confidence_threshold": self.confidence_threshold,
            "requires_human_review": self.requires_human_review,
        }


def compare_reviews(
    deterministic: ReviewResult,
    ai: ReviewResult,
    *,
    known_evidence_ids: set[str] | None = None,
    confidence_threshold: float = 0.85,
) -> ReviewComparison:
    """Compare local and AI reviews, separating safety issues from wording."""
    comparison = ReviewComparison(
        deterministic_recommendation=deterministic.recommendation.value,
        ai_recommendation=ai.recommendation.value,
        confidence_threshold=confidence_threshold,
    )

    if deterministic.recommendation is not ai.recommendation:
        comparison.material_disagreements.append(
            "Final recommendation differs between deterministic review and AI review."
        )

    if deterministic.confidence_score < confidence_threshold:
        comparison.material_disagreements.append(
            f"Deterministic review confidence {deterministic.confidence_score:.2f} "
            f"is below threshold {confidence_threshold:.2f}."
        )
    if ai.confidence_score < confidence_threshold:
        comparison.material_disagreements.append(
            f"AI review confidence {ai.confidence_score:.2f} is below "
            f"threshold {confidence_threshold:.2f}."
        )

    ai_cited = cited_evidence_ids(ai)
    if known_evidence_ids is not None:
        comparison.invalid_evidence_ids = sorted(ai_cited - known_evidence_ids)
        if comparison.invalid_evidence_ids:
            comparison.material_disagreements.append(
                "AI review cites evidence ids that do not exist in the case."
            )

        missing_ai_refs = [
            detail.id
            for detail in ai.criteria_detail
            if detail.status is CriterionStatus.MET
            and not detail.supporting_evidence_ids
        ]
        if missing_ai_refs:
            comparison.material_disagreements.append(
                "AI review marks criterion/criteria as met without evidence ids: "
                + ", ".join(missing_ai_refs)
                + "."
            )

    if (
        deterministic.recommendation is Recommendation.INSUFFICIENT_INFORMATION
        and ai.recommendation is Recommendation.APPROVE
        and (not ai_cited or ai.confidence_score < confidence_threshold)
    ):
        comparison.material_disagreements.append(
            "Deterministic review found insufficient information, while AI "
            "approved without strong cited evidence."
        )

    local_status = _criterion_statuses(deterministic)
    ai_status = _criterion_statuses(ai)
    if local_status != ai_status and deterministic.recommendation is ai.recommendation:
        comparison.non_material_differences.append(
            "Rule-level criterion statuses differ, but the final recommendation matches."
        )

    if _norm_list(deterministic.matched_criteria) != _norm_list(ai.matched_criteria):
        comparison.non_material_differences.append("Matched criteria wording/list differs.")
    if _norm_list(deterministic.missing_criteria) != _norm_list(ai.missing_criteria):
        comparison.non_material_differences.append("Missing criteria wording/list differs.")

    return comparison


def cited_evidence_ids(review: ReviewResult) -> set[str]:
    """Return all EvidenceReference ids cited by a review result."""
    ids = {
        *review.matched_evidence_ids,
        *review.missing_evidence_ids,
        *review.rationale_evidence_ids,
        *review.recommendation_evidence_ids,
    }
    for key, values in (review.evidence_refs or {}).items():
        if key == "retrieved_guidelines":
            continue
        ids.update(values)
    for detail in review.criteria_detail:
        ids.update(detail.supporting_evidence_ids)
    return {str(value).strip() for value in ids if str(value).strip()}


def _criterion_statuses(review: ReviewResult) -> dict[str, str]:
    return {
        detail.id: (detail.status.value if detail.status else "")
        for detail in review.criteria_detail
    }


def _norm_list(items: list[str]) -> list[str]:
    return sorted(" ".join(item.lower().split()) for item in items)
