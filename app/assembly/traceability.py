"""Traceability linking: attach evidence references to review and appeal.

Given a :class:`UnifiedCaseContext` (which holds the evidence inventory and the
field -> evidence map), this module annotates a :class:`ReviewResult` and an
:class:`AppealLetter` with the evidence-reference ids that support each part.

It never invents evidence: ids are drawn only from the context's inventory, and
a criterion/section is linked to evidence only when the underlying field has
real source-backed evidence. Anything unsupported is left explicitly empty so
the UI/export can flag it.
"""

from __future__ import annotations

from app.models.appeal_letter import AppealLetter
from app.models.review_result import ReviewResult
from app.models.unified_case_context import UnifiedCaseContext

# Which structured fields support which kinds of conclusions.
_CRITERION_FIELDS = (
    "diagnosis",
    "icd10_codes",
    "requested_service",
    "cpt_codes",
)
_RATIONALE_FIELDS = ("denial_reason", "decision")


def _collect_ids(context: UnifiedCaseContext, fields) -> list[str]:
    ids: list[str] = []
    for field in fields:
        for ev in context.evidence_for_field(field):
            if ev.evidence_id not in ids:
                ids.append(ev.evidence_id)
    return ids


def annotate_review(
    review: ReviewResult,
    context: UnifiedCaseContext,
) -> ReviewResult:
    """Attach evidence ids to a review result (in place) and return it."""
    matched_ids = _collect_ids(context, _CRITERION_FIELDS)
    rationale_ids = _collect_ids(context, _RATIONALE_FIELDS)

    review.matched_evidence_ids = matched_ids
    review.rationale_evidence_ids = rationale_ids
    review.recommendation_evidence_ids = list(
        dict.fromkeys(matched_ids + rationale_ids)
    )
    # Missing criteria, by definition, lack supporting evidence -> empty list.
    review.missing_evidence_ids = []
    return review


def annotate_appeal(
    appeal: AppealLetter,
    context: UnifiedCaseContext,
) -> AppealLetter:
    """Attach per-section evidence ids + citations to an appeal (in place)."""
    section_map: dict[str, list[str]] = {}

    # Patient Information -> identity fields.
    section_map["Patient Information"] = _collect_ids(
        context, ("patient_name", "member_id", "date_of_birth", "insurance_company")
    )
    # Clinical Background -> diagnosis evidence.
    section_map["Clinical Background"] = _collect_ids(
        context, ("diagnosis", "icd10_codes")
    )
    # Requested Service -> service/codes evidence.
    section_map["Requested Service"] = _collect_ids(
        context, ("requested_service", "cpt_codes")
    )
    # Reason For Appeal / Guideline Support -> clinical + rationale evidence.
    section_map["Reason For Appeal"] = _collect_ids(
        context, ("decision", "denial_reason", "diagnosis", "requested_service")
    )
    section_map["Guideline Support"] = _collect_ids(
        context, _CRITERION_FIELDS
    )

    # Drop empty sections so the map only contains supported sections.
    section_map = {k: v for k, v in section_map.items() if v}

    all_ids: list[str] = []
    for ids in section_map.values():
        for i in ids:
            if i not in all_ids:
                all_ids.append(i)

    # Human-readable citations for every cited evidence id.
    citations: list[str] = []
    for ev_id in all_ids:
        ev = context.evidence_by_id(ev_id)
        if ev:
            cite = f"{ev.citation()} {ev.normalized_fact}".strip()
            if cite not in citations:
                citations.append(cite)

    appeal.section_evidence = section_map
    appeal.evidence_ids = all_ids
    appeal.citations = citations
    return appeal
