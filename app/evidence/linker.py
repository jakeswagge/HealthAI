"""Evidence linking: attach source-backed evidence ids to review + appeal.

Rather than entangle the review/appeal engines with the evidence store, this
module post-processes their outputs against a :class:`UnifiedCaseContext`:

- ``link_review`` populates ``ReviewResult.evidence_refs`` (matched/missing
  criteria, denial rationale, recommendation) with the evidence ids whose
  quoted text / fact best supports each item.
- ``link_appeal`` populates ``AppealLetter.section_evidence`` per appeal
  section and flags any statement that lacks supporting evidence.

Linking is deterministic keyword/overlap matching - it never invents evidence.
If nothing matches, the link is simply empty (and, for appeals, surfaced as an
"unsupported statement" so the quality gate can catch it).
"""

from __future__ import annotations

import re

from app.models.appeal_letter import AppealLetter
from app.models.evidence_reference import EvidenceReference
from app.models.review_result import CriterionEvaluation, ReviewResult
from app.models.unified_case_context import UnifiedCaseContext

_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "is",
    "was", "were", "with", "without", "not", "no", "any", "least", "prior",
    "documented", "documentation", "evidence", "criteria", "criterion", "this",
    "that", "which", "be", "been", "as", "by", "are", "from",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _best_matches(
    text: str,
    evidence: list[EvidenceReference],
    threshold: int = 1,
    limit: int = 3,
) -> list[str]:
    """Return evidence ids whose content best overlaps ``text``."""
    target = _tokens(text)
    if not target:
        return []
    scored: list[tuple[int, str]] = []
    for ev in evidence:
        hay = _tokens(f"{ev.normalized_fact} {ev.quoted_text}")
        overlap = len(target & hay)
        if overlap >= threshold:
            scored.append((overlap, ev.evidence_id))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [eid for _, eid in scored[:limit]]


def _criterion_fact_types(detail: CriterionEvaluation) -> tuple[str, ...]:
    """Return preferred evidence fact types for a criterion row."""
    marker = f"{detail.id} {detail.description}".lower()
    if "specialist" in marker or "rheumatologist" in marker:
        return ("criterion_specialist", "specialist_status", "provider_role")
    if "tb_screen" in marker or "tuberculosis" in marker:
        return ("tb_screen_result", "criterion_tb_screen")
    if "dmard" in marker or "methotrexate" in marker or "step_therapy" in marker:
        return ("criterion_step_therapy", "step_therapy_status")
    if "diagnosis" in marker or "rheumatoid arthritis" in marker:
        return ("diagnosis", "icd10_codes")
    return ()


def _ids_for_fact_types(
    evidence: list[EvidenceReference],
    fact_types: tuple[str, ...],
) -> list[str]:
    ids: list[str] = []
    for fact_type in fact_types:
        for ev in evidence:
            if ev.fact_type == fact_type and ev.evidence_id not in ids:
                ids.append(ev.evidence_id)
    return ids


def _ids_for_fact_types_limited(
    evidence: list[EvidenceReference],
    fact_types: tuple[str, ...],
    limit: int = 5,
) -> list[str]:
    return _ids_for_fact_types(evidence, fact_types)[:limit]


def _detail_evidence_ids(
    detail: CriterionEvaluation,
    evidence: list[EvidenceReference],
) -> list[str]:
    ids = _ids_for_fact_types(evidence, _criterion_fact_types(detail))
    if not ids and detail.note:
        ids = _best_matches(detail.note, evidence, threshold=1)
    valid = {ev.evidence_id for ev in evidence}
    existing = [eid for eid in detail.supporting_evidence_ids if eid in valid]
    return _dedupe(ids + existing)


def _assign_detail_evidence_ids(
    detail: CriterionEvaluation,
    evidence: list[EvidenceReference],
) -> None:
    linked_ids = _detail_evidence_ids(detail, evidence)
    if not linked_ids:
        return
    if detail.status and detail.status.value == "not_met":
        detail.not_met_evidence_ids = linked_ids
    detail.supporting_evidence_ids = linked_ids


def link_review(review: ReviewResult, context: UnifiedCaseContext) -> ReviewResult:
    """Populate ``review.evidence_refs`` from the assembled context.

    Returns the same (mutated) ReviewResult for convenience.
    """
    evidence = context.evidence
    refs: dict[str, list[str]] = {}
    detail_by_description = {d.description: d for d in review.criteria_detail}

    for detail in review.criteria_detail:
        _assign_detail_evidence_ids(detail, evidence)

    matched_ids: list[str] = []
    for crit in review.matched_criteria:
        detail = detail_by_description.get(crit)
        if detail:
            matched_ids.extend(detail.supporting_evidence_ids)
        matched_ids.extend(_best_matches(crit, evidence))
        if detail and detail.note:
            matched_ids.extend(_best_matches(detail.note, evidence, threshold=1))
    if matched_ids:
        refs["matched_criteria"] = _dedupe(matched_ids)

    missing_ids: list[str] = []
    for crit in review.missing_criteria:
        detail = detail_by_description.get(crit)
        if detail:
            missing_ids.extend(detail.supporting_evidence_ids)
            missing_ids.extend(detail.not_met_evidence_ids)
            if detail.note:
                missing_ids.extend(_best_matches(detail.note, evidence, threshold=1))
        else:
            missing_ids.extend(_best_matches(crit, evidence, threshold=2))
    if missing_ids:
        refs["missing_criteria"] = _dedupe(missing_ids)

    # Denial rationale: link to denial_reason / decision evidence.
    denial_evidence = [
        e.evidence_id for e in evidence if e.fact_type in ("denial_reason", "decision")
    ]
    if denial_evidence:
        refs["denial_rationale"] = denial_evidence

    # Recommendation: link to diagnosis + requested_service evidence.
    rec_evidence = [
        e.evidence_id
        for e in evidence
        if e.fact_type in ("diagnosis", "requested_service")
    ]
    if rec_evidence:
        refs["recommendation"] = rec_evidence

    review.evidence_refs = refs
    return review


def link_appeal(
    appeal: AppealLetter, context: UnifiedCaseContext
) -> tuple[AppealLetter, list[str]]:
    """Populate ``appeal.section_evidence`` and return unsupported statements.

    The second return value is a list of human-readable statements that could
    not be tied to any evidence; an empty list means the appeal is fully
    traceable.
    """
    evidence = context.evidence
    section_evidence: dict[str, list[str]] = {}
    unsupported: list[str] = []

    # Map the structured appeal fields to evidence.
    section_evidence["clinical_summary"] = _best_matches(
        appeal.clinical_summary, evidence, limit=5
    ) or _ids_for_fact_types_limited(
        evidence,
        (
            "diagnosis",
            "requested_service",
            "step_therapy_status",
            "tb_screen_result",
            "provider_role",
            "specialist_status",
            "criterion_specialist",
        ),
        limit=5,
    )
    section_evidence["appeal_reason"] = _best_matches(
        appeal.appeal_reason, evidence, limit=5
    ) or _ids_for_fact_types_limited(
        evidence,
        (
            "denial_reason",
            "decision",
            "claim_denial_reason",
            "prior_auth_status",
        ),
        limit=5,
    )
    for i, item in enumerate(appeal.guideline_support):
        ids = _best_matches(item, evidence)
        if ids:
            section_evidence[f"guideline_support[{i}]"] = ids

    # Missing-information items are, by definition, not evidence-backed (they
    # describe gaps); we record them as honest gaps, not unsupported claims.
    # Any clinical_summary / appeal_reason with zero links is flagged.
    if appeal.clinical_summary.strip() and not section_evidence["clinical_summary"]:
        unsupported.append("clinical_summary")
    if appeal.appeal_reason.strip() and not section_evidence["appeal_reason"]:
        unsupported.append("appeal_reason")

    # Prune empty keys for a clean record.
    appeal.section_evidence = {k: v for k, v in section_evidence.items() if v}
    return appeal, unsupported


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
