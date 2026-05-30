"""EvidenceQualityEngine: score evidence and detect quality issues.

Per-reference scoring dimensions (each 0.0-1.0):

- completeness: does the reference carry a quote, a normalized fact, and a
  meaningful value? (the substance needed to support a claim)
- relevance:    is the fact one of the recognized clinical/administrative
  fields, and does the quote actually contain the value?
- consistency:  does this reference agree with other references for the same
  fact_type? (lowered when the case has conflicting values)
- traceability: does it have a source document, page, and verbatim quote?

overall = weighted mean of the four. Issues detected across the set:
- WEAK evidence (low overall)
- DUPLICATE evidence (same fact_type + value)
- CONFLICTING support (same fact_type, different values)
- MISSING support (a fact has no quote)
- UNSUPPORTED appeal statements (optional; when an appeal is supplied)
"""

from __future__ import annotations

import re

from app.models.evidence_quality import (
    WEAK_EVIDENCE_THRESHOLD,
    EvidenceQualityAssessment,
)
from app.models.evidence_reference import EvidenceReference

# Recognized logical fact types (relevance signal).
_KNOWN_FACTS = {
    "patient_name", "member_id", "date_of_birth", "diagnosis",
    "requested_service", "insurance_company", "physician_name", "decision",
    "denial_reason", "icd10_codes", "cpt_codes",
}

# Score weights.
_W_COMPLETE = 0.30
_W_RELEVANCE = 0.25
_W_CONSISTENCY = 0.25
_W_TRACE = 0.20


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _value_of(ref: EvidenceReference) -> str:
    return ref.normalized_fact.split(": ", 1)[-1] if ref.normalized_fact else ""


class EvidenceQualityEngine:
    """Score evidence references and surface quality issues."""

    def __init__(self, weak_threshold: float = WEAK_EVIDENCE_THRESHOLD) -> None:
        self.weak_threshold = weak_threshold

    def assess_all(
        self,
        evidence: list[EvidenceReference],
        case_id: str = "",
    ) -> list[EvidenceQualityAssessment]:
        """Assess every reference, using the full set for consistency checks."""
        # Group by fact_type for consistency / duplicate / conflict detection.
        by_fact: dict[str, list[EvidenceReference]] = {}
        for ev in evidence:
            by_fact.setdefault(ev.fact_type or "other", []).append(ev)

        assessments: list[EvidenceQualityAssessment] = []
        for ev in evidence:
            assessments.append(self._assess_one(ev, by_fact, case_id))
        return assessments

    def _assess_one(
        self,
        ev: EvidenceReference,
        by_fact: dict[str, list[EvidenceReference]],
        case_id: str,
    ) -> EvidenceQualityAssessment:
        issues: list[str] = []
        value = _value_of(ev)
        quote = ev.quoted_text or ""

        # --- completeness ---
        completeness = 0.0
        if quote.strip():
            completeness += 0.5
        if value:
            completeness += 0.3
        if ev.fact_type:
            completeness += 0.2
        if not quote.strip():
            issues.append("missing support: no quoted source text")

        # --- relevance ---
        relevance = 1.0 if (ev.fact_type in _KNOWN_FACTS) else 0.5
        # Quote should contain the value (for scalar facts).
        if value and quote and _norm(value) not in _norm(quote):
            relevance = min(relevance, 0.6)
            issues.append("weak evidence: value not found within the quoted text")

        # --- consistency ---
        siblings = by_fact.get(ev.fact_type or "other", [])
        distinct_values = {_norm(_value_of(s)) for s in siblings if _value_of(s)}
        same_value = [s for s in siblings if _norm(_value_of(s)) == _norm(value)]
        if len(distinct_values) > 1 and ev.fact_type not in {"icd10_codes", "cpt_codes"}:
            consistency = 0.5
            issues.append("conflicting support: other documents disagree on this fact")
        else:
            consistency = 1.0
        if len(same_value) > 1:
            issues.append("duplicate evidence: same fact appears multiple times")

        # --- traceability ---
        traceability = 0.0
        if ev.source_document_id:
            traceability += 0.4
        if ev.page_number and ev.page_number >= 1:
            traceability += 0.3
        if quote.strip():
            traceability += 0.3
        if not ev.source_document_id:
            issues.append("traceability gap: no source document id")

        overall = round(
            _W_COMPLETE * completeness
            + _W_RELEVANCE * relevance
            + _W_CONSISTENCY * consistency
            + _W_TRACE * traceability,
            4,
        )
        # Blend in the reference's own confidence as a soft cap signal.
        overall = round(min(overall, 0.5 + 0.5 * ev.confidence_score) if ev.confidence_score else overall, 4)

        if overall < self.weak_threshold:
            issues.append(f"weak evidence: overall quality {overall:.2f} below threshold")

        return EvidenceQualityAssessment(
            evidence_id=ev.evidence_id,
            case_id=case_id or ev.case_id,
            completeness_score=completeness,
            relevance_score=relevance,
            consistency_score=consistency,
            traceability_score=traceability,
            overall_score=overall,
            issues=_dedupe(issues),
        )

    # ------------------------------------------------------------------ #
    # Appeal support check
    # ------------------------------------------------------------------ #
    def unsupported_appeal_statements(
        self,
        appeal,
        evidence: list[EvidenceReference],
    ) -> list[str]:
        """Return appeal sections that have no supporting evidence reference.

        Uses the appeal's ``section_evidence`` map (populated by the evidence
        linker). A non-empty section with no ids, or ids not present in the
        evidence inventory, is reported as unsupported.
        """
        valid_ids = {e.evidence_id for e in evidence}
        unsupported: list[str] = []
        section_evidence = getattr(appeal, "section_evidence", {}) or {}

        # clinical_summary + appeal_reason are substantive claims.
        for section in ("clinical_summary", "appeal_reason"):
            text = getattr(appeal, section, "") or ""
            if not text.strip():
                continue
            ids = section_evidence.get(section, [])
            if not ids or not any(i in valid_ids for i in ids):
                unsupported.append(section)
        return unsupported


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
