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
from app.models.review_result import ReviewResult
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


def link_review(review: ReviewResult, context: UnifiedCaseContext) -> ReviewResult:
    """Populate ``review.evidence_refs`` from the assembled context.

    Returns the same (mutated) ReviewResult for convenience.
    """
    evidence = context.evidence
    refs: dict[str, list[str]] = {}
    detail_by_description = {d.description: d for d in review.criteria_detail}

    matched_ids: list[str] = []
    for crit in review.matched_criteria:
        matched_ids.extend(_best_matches(crit, evidence))
        detail = detail_by_description.get(crit)
        if detail and detail.note:
            matched_ids.extend(_best_matches(detail.note, evidence, threshold=1))
    if matched_ids:
        refs["matched_criteria"] = _dedupe(matched_ids)

    missing_ids: list[str] = []
    for crit in review.missing_criteria:
        missing_ids.extend(_best_matches(crit, evidence))
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
    )
    section_evidence["appeal_reason"] = _best_matches(
        appeal.appeal_reason, evidence, limit=5
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
