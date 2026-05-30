"""CaseAssemblyEngine: merge multi-document evidence into a unified context.

Responsibilities:
- Run the :class:`EvidenceExtractor` over every document.
- De-duplicate identical facts (same fact_type + value).
- Resolve a single "best" value per fact (preferring authoritative document
  types, then confidence), recording its supporting evidence.
- Detect conflicts (same fact, different values) with severity levels.
- Identify missing required information.
- Synthesize a :class:`PatientCase` with per-field source attribution.
- Produce a :class:`UnifiedCaseContext` (evidence inventory + everything above).

Deterministic and offline; no model calls.
"""

from __future__ import annotations

from app.evidence.extractor import EvidenceExtractor
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.conflict_report import (
    ConflictReport,
    ConflictSeverity,
    FactConflict,
)
from app.models.evidence_reference import EvidenceReference
from app.models.patient_case import Decision, FieldSource, PatientCase
from app.models.unified_case_context import ResolvedFact, UnifiedCaseContext

# Single-value facts (one canonical value expected across the case).
_SCALAR_FACTS = (
    "patient_name",
    "member_id",
    "date_of_birth",
    "diagnosis",
    "requested_service",
    "insurance_company",
    "physician_name",
    "decision",
    "denial_reason",
)
_LIST_FACTS = ("icd10_codes", "cpt_codes")

# Required facts for a complete prior-authorization case.
_REQUIRED_FACTS = (
    "patient_name",
    "member_id",
    "diagnosis",
    "requested_service",
)

# Severity assigned to a conflict in a given fact.
_CONFLICT_SEVERITY: dict[str, ConflictSeverity] = {
    "member_id": ConflictSeverity.HIGH,
    "patient_name": ConflictSeverity.HIGH,
    "date_of_birth": ConflictSeverity.HIGH,
    "diagnosis": ConflictSeverity.HIGH,
    "requested_service": ConflictSeverity.MEDIUM,
    "denial_reason": ConflictSeverity.MEDIUM,
    "insurance_company": ConflictSeverity.MEDIUM,
    "physician_name": ConflictSeverity.LOW,
    "decision": ConflictSeverity.MEDIUM,
}

# Document types considered more authoritative for a given fact.
_AUTHORITATIVE: dict[str, tuple[DocumentCategory, ...]] = {
    "diagnosis": (DocumentCategory.CLINICAL_NOTE, DocumentCategory.IMAGING_REPORT),
    "denial_reason": (DocumentCategory.DENIAL_LETTER,),
    "decision": (DocumentCategory.DENIAL_LETTER,),
    "requested_service": (DocumentCategory.PRIOR_AUTH_FORM, DocumentCategory.DENIAL_LETTER),
    "member_id": (DocumentCategory.DENIAL_LETTER, DocumentCategory.PRIOR_AUTH_FORM),
}


def _norm_value(fact_type: str, value: str) -> str:
    """Normalize a value for equality comparison (case/space-insensitive)."""
    v = " ".join(value.strip().split())
    if fact_type in ("member_id", "icd10_codes", "cpt_codes"):
        return v.upper()
    return v.lower()


class CaseAssemblyEngine:
    """Assemble multiple CaseDocuments into a UnifiedCaseContext."""

    def __init__(self, extractor: EvidenceExtractor | None = None) -> None:
        self.extractor = extractor or EvidenceExtractor()

    def assemble(
        self, case_id: str, documents: list[CaseDocument]
    ) -> UnifiedCaseContext:
        """Assemble a unified, evidence-backed context for a case."""
        doc_by_id = {d.document_id: d for d in documents}

        # 1. Gather + de-duplicate evidence.
        all_evidence: list[EvidenceReference] = []
        for doc in documents:
            all_evidence.extend(self.extractor.extract(doc))
        evidence = self._dedupe(all_evidence)

        # 2. Group evidence by fact type.
        by_fact: dict[str, list[EvidenceReference]] = {}
        for ev in evidence:
            by_fact.setdefault(ev.fact_type or "unknown", []).append(ev)

        # 3. Resolve scalar facts + detect conflicts.
        resolved: dict[str, ResolvedFact] = {}
        conflicts: list[FactConflict] = []

        for fact in _SCALAR_FACTS:
            refs = by_fact.get(fact, [])
            if not refs:
                continue
            distinct = self._distinct_values(fact, refs)
            chosen = self._choose(fact, refs, doc_by_id)
            resolved[fact] = ResolvedFact(
                fact_type=fact,
                value=chosen.normalized_fact.split(": ", 1)[-1],
                evidence_id=chosen.evidence_id,
                source_filename=chosen.source_filename,
                source_page=chosen.page_number,
                alternatives=[v for v in distinct if v != _norm_value(fact, chosen.normalized_fact.split(": ", 1)[-1])],
            )
            if len(distinct) > 1:
                conflicts.append(
                    FactConflict(
                        conflict_id=f"CFL-{case_id}-{fact}",
                        fact_type=fact,
                        severity=_CONFLICT_SEVERITY.get(fact, ConflictSeverity.LOW),
                        values=self._display_values(fact, refs),
                        evidence_ids=[r.evidence_id for r in refs],
                        description=(
                            f"Conflicting values for '{fact}' across documents: "
                            + "; ".join(self._display_values(fact, refs))
                        ),
                    )
                )

        # 4. Missing information.
        missing = [
            f"No evidence found for required field: {fact}"
            for fact in _REQUIRED_FACTS
            if fact not in resolved
        ]

        conflict_report = ConflictReport(case_id=case_id, conflicts=conflicts)

        # 5. Synthesize a backward-compatible PatientCase with sources.
        patient_case = self._synthesize_case(case_id, resolved, by_fact)

        return UnifiedCaseContext(
            case_id=case_id,
            document_ids=[d.document_id for d in documents],
            evidence=evidence,
            resolved_facts=resolved,
            conflict_report=conflict_report,
            missing_information=missing,
            patient_case=patient_case,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _dedupe(evidence: list[EvidenceReference]) -> list[EvidenceReference]:
        """Remove duplicate facts (same fact_type + value + document + page)."""
        seen: set[tuple] = set()
        out: list[EvidenceReference] = []
        for ev in evidence:
            key = (
                ev.fact_type,
                _norm_value(ev.fact_type or "", ev.normalized_fact.split(": ", 1)[-1]),
                ev.source_document_id,
                ev.page_number,
            )
            if key not in seen:
                seen.add(key)
                out.append(ev)
        return out

    @staticmethod
    def _value_of(ref: EvidenceReference) -> str:
        return ref.normalized_fact.split(": ", 1)[-1]

    def _distinct_values(self, fact: str, refs: list[EvidenceReference]) -> list[str]:
        out: list[str] = []
        for r in refs:
            nv = _norm_value(fact, self._value_of(r))
            if nv not in out:
                out.append(nv)
        return out

    def _display_values(self, fact: str, refs: list[EvidenceReference]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for r in refs:
            v = self._value_of(r)
            nv = _norm_value(fact, v)
            if nv not in seen:
                seen.add(nv)
                out.append(f"{v} {r.citation()}")
        return out

    def _choose(
        self,
        fact: str,
        refs: list[EvidenceReference],
        doc_by_id: dict[str, CaseDocument],
    ) -> EvidenceReference:
        """Pick the best evidence for a fact: authoritative doc type, then conf."""
        authoritative = _AUTHORITATIVE.get(fact, ())

        def score(ref: EvidenceReference) -> tuple:
            doc = doc_by_id.get(ref.source_document_id)
            is_auth = 1 if (doc and doc.document_type in authoritative) else 0
            return (is_auth, ref.confidence_score)

        return max(refs, key=score)

    def _synthesize_case(
        self,
        case_id: str,
        resolved: dict[str, ResolvedFact],
        by_fact: dict[str, list[EvidenceReference]],
    ) -> PatientCase:
        """Build a PatientCase from resolved facts, attaching field sources."""

        def val(fact: str):
            rf = resolved.get(fact)
            return rf.value if rf else None

        # Code lists: union of all distinct values seen.
        def codes(fact: str) -> list[str]:
            out: list[str] = []
            for r in by_fact.get(fact, []):
                v = self._value_of(r).upper()
                if v not in out:
                    out.append(v)
            return out

        decision_raw = val("decision") or "unknown"

        field_sources: dict[str, FieldSource] = {}
        for fact, rf in resolved.items():
            field_sources[fact] = FieldSource(
                source_document=rf.source_filename,
                source_page=rf.source_page,
                evidence_id=rf.evidence_id,
            )

        case = PatientCase(
            patient_name=val("patient_name"),
            member_id=val("member_id"),
            date_of_birth=val("date_of_birth"),
            diagnosis=val("diagnosis"),
            icd10_codes=codes("icd10_codes"),
            requested_service=val("requested_service"),
            cpt_codes=codes("cpt_codes"),
            insurance_company=val("insurance_company"),
            decision=decision_raw,
            denial_reason=val("denial_reason") if decision_raw == "denied" else None,
            physician_name=val("physician_name"),
            field_sources=field_sources,
        )
        # Completeness-based confidence so downstream UIs show something useful.
        if case.confidence_score <= 0.0:
            case.confidence_score = max(0.1, case.completeness)
        return case
