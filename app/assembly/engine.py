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
from app.models.clinical_fact import (
    ClinicalFact,
    ConflictStatus,
    DiagnosisState,
    StepTherapyState,
    TBScreenState,
    clinical_facts_from_evidence,
    fact_ids_for_evidence,
)
from app.models.conflict_report import (
    ConflictReport,
    ConflictSeverity,
    FactConflict,
)
from app.models.evidence_reference import EvidenceReference
from app.models.patient_case import Decision, FieldSource, NormalizedField, PatientCase
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
    "tb_screen_result",
    "provider_role",
    "specialist_status",
    "step_therapy_status",
    "prior_auth_status",
    "claim_denial_reason",
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
    "tb_screen_result": ConflictSeverity.HIGH,
    "provider_role": ConflictSeverity.MEDIUM,
    "specialist_status": ConflictSeverity.MEDIUM,
    "step_therapy_status": ConflictSeverity.HIGH,
    "prior_auth_status": ConflictSeverity.MEDIUM,
    "claim_denial_reason": ConflictSeverity.MEDIUM,
}

# Document types considered more authoritative for a given fact.
_AUTHORITATIVE: dict[str, tuple[DocumentCategory, ...]] = {
    "diagnosis": (DocumentCategory.CLINICAL_NOTE, DocumentCategory.IMAGING_REPORT),
    "denial_reason": (DocumentCategory.DENIAL_LETTER,),
    "decision": (DocumentCategory.DENIAL_LETTER,),
    "requested_service": (DocumentCategory.PRIOR_AUTH_FORM, DocumentCategory.DENIAL_LETTER),
    "member_id": (DocumentCategory.DENIAL_LETTER, DocumentCategory.PRIOR_AUTH_FORM),
    "tb_screen_result": (DocumentCategory.LAB_RESULT, DocumentCategory.CLINICAL_NOTE),
    "provider_role": (DocumentCategory.CLINICAL_NOTE, DocumentCategory.REFERRAL),
    "specialist_status": (DocumentCategory.CLINICAL_NOTE, DocumentCategory.REFERRAL),
    "step_therapy_status": (DocumentCategory.CLINICAL_NOTE,),
    "prior_auth_status": (DocumentCategory.DENIAL_LETTER,),
    "claim_denial_reason": (DocumentCategory.DENIAL_LETTER,),
}

_CLINICAL_SERVICE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Humira (adalimumab)", ("humira", "adalimumab")),
)


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

        return self._assemble_from_evidence(
            case_id,
            evidence,
            doc_by_id,
            document_ids=[d.document_id for d in documents],
            allow_document_text_healing=True,
        )

    def synthesize_from_evidence(
        self,
        case_id: str,
        evidence: list[EvidenceReference],
        documents: list[CaseDocument] | None = None,
    ) -> UnifiedCaseContext:
        """Assemble a context from a GIVEN evidence list (not re-extracted).

        Milestone 13 (governance enforcement): downstream review/appeal must run
        only on governance-permitted evidence. This synthesizes a
        :class:`UnifiedCaseContext` (and its :class:`PatientCase`) from exactly
        the evidence references supplied - e.g. the included subset of an
        :class:`ApprovedEvidenceSet`. Rejected/excluded evidence therefore cannot
        influence the resulting case, conflicts, or missing-information list.

        ``documents`` is optional; when provided it is used only to score
        authoritative document types during conflict resolution (same logic as
        :meth:`assemble`). When omitted, resolution falls back to confidence.
        """
        doc_by_id = {d.document_id: d for d in (documents or [])}
        deduped = self._dedupe(list(evidence))
        document_ids: list[str] = []
        for ev in deduped:
            if ev.source_document_id not in document_ids:
                document_ids.append(ev.source_document_id)
        return self._assemble_from_evidence(
            case_id,
            deduped,
            doc_by_id,
            document_ids=document_ids,
            allow_document_text_healing=False,
        )

    def _assemble_from_evidence(
        self,
        case_id: str,
        evidence: list[EvidenceReference],
        doc_by_id: dict[str, CaseDocument],
        document_ids: list[str],
        allow_document_text_healing: bool,
    ) -> UnifiedCaseContext:
        """Shared assembly core: group -> resolve -> synthesize a context."""
        evidence = self._heal_requested_service_evidence(
            case_id, evidence, doc_by_id, allow_document_text_healing
        )

        # 2. Group evidence by fact type.
        by_fact: dict[str, list[EvidenceReference]] = {}
        for ev in evidence:
            by_fact.setdefault(ev.fact_type or "unknown", []).append(ev)

        clinical_facts = clinical_facts_from_evidence(evidence)

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
                confidence_score=chosen.confidence_score,
                alternatives=[v for v in distinct if v != _norm_value(fact, chosen.normalized_fact.split(": ", 1)[-1])],
            )
            if len(distinct) > 1:
                evidence_ids = [r.evidence_id for r in refs]
                fact_ids = fact_ids_for_evidence(clinical_facts, evidence_ids)
                conflicts.append(
                    FactConflict(
                        conflict_id=f"CFL-{case_id}-{fact}",
                        fact_type=fact,
                        severity=_CONFLICT_SEVERITY.get(fact, ConflictSeverity.LOW),
                        values=self._display_values(fact, refs),
                        evidence_ids=evidence_ids,
                        clinical_fact_ids=fact_ids,
                        description=(
                            f"Conflicting values for '{fact}' across documents: "
                            + "; ".join(self._display_values(fact, refs))
                            + ". Human review is required before downstream reliance."
                        ),
                        requires_human_review_reason=(
                            f"Unresolved {fact} conflict requires human review."
                        ),
                    )
                )

        conflicts.extend(
            self._semantic_conflicts(case_id, by_fact, clinical_facts)
        )
        self._mark_conflicted_facts(clinical_facts, conflicts)

        # 4. Missing information.
        missing = [
            f"No evidence found for required field: {fact}"
            for fact in _REQUIRED_FACTS
            if fact not in resolved
        ]

        conflict_report = ConflictReport(case_id=case_id, conflicts=conflicts)

        # 5. Synthesize a backward-compatible PatientCase with sources.
        patient_case = self._synthesize_case(
            case_id, resolved, by_fact, clinical_facts
        )
        self._apply_conflict_confidence(patient_case, conflicts)

        return UnifiedCaseContext(
            case_id=case_id,
            document_ids=document_ids,
            evidence=evidence,
            clinical_facts=clinical_facts,
            resolved_facts=resolved,
            conflict_report=conflict_report,
            missing_information=missing,
            patient_case=patient_case,
        )

    def _heal_requested_service_evidence(
        self,
        case_id: str,
        evidence: list[EvidenceReference],
        doc_by_id: dict[str, CaseDocument],
        allow_document_text_healing: bool,
    ) -> list[EvidenceReference]:
        """Add traceable requested-service evidence from known drug tokens.

        The governance path passes ``allow_document_text_healing=False`` so
        rejected/excluded raw document text cannot re-enter downstream review.
        """
        if any(ev.fact_type == "requested_service" for ev in evidence):
            return evidence
        if not allow_document_text_healing:
            return evidence

        candidates: list[tuple[str, str, CaseDocument | None]] = []
        for ev in evidence:
            haystack = " ".join(
                part for part in (ev.normalized_fact, ev.quoted_text) if part
            )
            if haystack:
                candidates.append((haystack, ev.quoted_text or haystack, None))

        if allow_document_text_healing:
            for doc in doc_by_id.values():
                for page in doc.pages():
                    for line in page.splitlines():
                        if line.strip():
                            candidates.append((line, line.strip(), doc))

        for haystack, quote, doc in candidates:
            low = haystack.lower()
            for canonical, tokens in _CLINICAL_SERVICE_TOKENS:
                if any(token in low for token in tokens):
                    source_doc = doc
                    if source_doc is None and evidence:
                        source_doc = doc_by_id.get(evidence[0].source_document_id)
                    if source_doc is None:
                        continue
                    healed = EvidenceReference(
                        case_id=case_id,
                        source_document_id=source_doc.document_id,
                        source_filename=source_doc.filename,
                        page_number=1,
                        section_label="Requested service inference",
                        quoted_text=quote,
                        normalized_fact=f"requested_service: {canonical}",
                        fact_type="requested_service",
                        confidence_score=0.7,
                    )
                    return [*evidence, healed]

        return evidence

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

    def _semantic_conflicts(
        self,
        case_id: str,
        by_fact: dict[str, list[EvidenceReference]],
        clinical_facts: list[ClinicalFact],
    ) -> list[FactConflict]:
        """Detect typed clinical-state conflicts beyond same-field strings."""
        conflicts: list[FactConflict] = []

        active_dx = [
            f for f in clinical_facts
            if f.domain.value == "DIAGNOSIS"
            and f.state == DiagnosisState.ACTIVE.value
        ]
        nonactive_dx = [
            f for f in clinical_facts
            if f.domain.value == "DIAGNOSIS"
            and f.state in {
                DiagnosisState.NEGATED.value,
                DiagnosisState.RULE_OUT.value,
                DiagnosisState.POSSIBLE.value,
            }
        ]
        for active in active_dx:
            contradicted = [
                f for f in nonactive_dx
                if _norm_value("diagnosis", f.value)
                == _norm_value("diagnosis", active.value)
            ]
            if contradicted:
                facts = [active, *contradicted]
                conflicts.append(
                    self._clinical_conflict(
                        case_id,
                        "diagnosis",
                        "diagnosis-assertion",
                        ConflictSeverity.HIGH,
                        facts,
                        "Active diagnosis evidence conflicts with negated/rule-out diagnosis evidence.",
                    )
                )

        provider_states = {
            f.state
            for f in clinical_facts
            if f.domain.value == "PROVIDER"
        }
        if {
            "SPECIALIST",
            "NON_SPECIALIST",
        } <= provider_states or {
            "CONSULTING_SPECIALIST",
            "NON_SPECIALIST",
        } <= provider_states:
            facts = [
                f for f in clinical_facts
                if f.domain.value == "PROVIDER"
                and f.state in {"SPECIALIST", "CONSULTING_SPECIALIST", "NON_SPECIALIST"}
            ]
            conflicts.append(
                self._clinical_conflict(
                    case_id,
                    "provider_role",
                    "specialist-vs-non-specialist",
                    ConflictSeverity.MEDIUM,
                    facts,
                    "Specialist and non-specialist provider evidence conflict.",
                )
            )

        requested = [
            ref for ref in by_fact.get("requested_service", [])
            if "humira" in self._value_of(ref).lower()
            or "adalimumab" in self._value_of(ref).lower()
        ]
        refused_step = [
            f for f in clinical_facts
            if f.domain.value == "STEP_THERAPY"
            and f.state in {
                StepTherapyState.REFUSED.value,
                StepTherapyState.NEVER_STARTED.value,
            }
        ]
        direct_biologic_refusal = [
            f for f in refused_step
            if "direct biologic" in f.quoted_text.lower()
            or "biologic therapy" in f.quoted_text.lower()
        ]
        if requested and direct_biologic_refusal:
            facts = direct_biologic_refusal
            evidence_ids = [r.evidence_id for r in requested]
            for fact in facts:
                evidence_ids.extend(fact.evidence_ids)
            conflicts.append(
                FactConflict(
                    conflict_id=f"CFL-{case_id}-treatment-recommendation",
                    fact_type="treatment_recommendation",
                    severity=ConflictSeverity.MEDIUM,
                    values=[
                        *self._display_values("requested_service", requested),
                        *[f"{f.state}: {f.value}" for f in facts],
                    ],
                    evidence_ids=list(dict.fromkeys(evidence_ids)),
                    clinical_fact_ids=[f.fact_id for f in facts],
                    description=(
                        "Requested biologic therapy conflicts with explicit "
                        "refusal or non-initiation of conventional DMARD therapy."
                    ),
                    requires_human_review_reason=(
                        "Treatment recommendation conflict requires human review."
                    ),
                )
            )

        # Existing same-fact conflict detection already catches TB and step
        # state clashes when they share the same legacy fact type. This helper
        # only adds semantic cross-fact gaps.
        return conflicts

    @staticmethod
    def _clinical_conflict(
        case_id: str,
        fact_type: str,
        suffix: str,
        severity: ConflictSeverity,
        facts: list[ClinicalFact],
        description: str,
    ) -> FactConflict:
        evidence_ids: list[str] = []
        values: list[str] = []
        for fact in facts:
            for ev_id in fact.evidence_ids:
                if ev_id not in evidence_ids:
                    evidence_ids.append(ev_id)
            text = f"{fact.state}: {fact.value}"
            if text not in values:
                values.append(text)
        return FactConflict(
            conflict_id=f"CFL-{case_id}-{fact_type}-{suffix}",
            fact_type=fact_type,
            severity=severity,
            values=values,
            evidence_ids=evidence_ids,
            clinical_fact_ids=[f.fact_id for f in facts],
            description=description,
            requires_human_review_reason=description,
        )

    @staticmethod
    def _mark_conflicted_facts(
        clinical_facts: list[ClinicalFact],
        conflicts: list[FactConflict],
    ) -> None:
        conflicted = {
            fact_id
            for conflict in conflicts
            for fact_id in conflict.clinical_fact_ids
        }
        for fact in clinical_facts:
            if fact.fact_id in conflicted:
                fact.conflict_status = ConflictStatus.CONFLICTED

    @staticmethod
    def _apply_conflict_confidence(
        case: PatientCase,
        conflicts: list[FactConflict],
    ) -> None:
        """Lower case confidence when unresolved conflicts remain."""
        if not conflicts:
            return
        if any(c.severity is ConflictSeverity.HIGH for c in conflicts):
            case.confidence_score = min(case.confidence_score, 0.65)
        else:
            case.confidence_score = min(case.confidence_score, 0.8)

    def _synthesize_case(
        self,
        case_id: str,
        resolved: dict[str, ResolvedFact],
        by_fact: dict[str, list[EvidenceReference]],
        clinical_facts: list[ClinicalFact],
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

        decision_raw = val("decision") or "pending"

        field_sources: dict[str, FieldSource] = {}
        raw_fields: dict[str, object] = {}
        normalized_fields: dict[str, NormalizedField] = {}
        for fact, rf in resolved.items():
            field_sources[fact] = FieldSource(
                source_document=rf.source_filename,
                source_page=rf.source_page,
                evidence_id=rf.evidence_id,
            )
            raw_fields[fact] = rf.value
            normalized_fields[fact] = NormalizedField(
                raw_value=rf.value,
                normalized_value=rf.value,
                source_evidence_ids=[rf.evidence_id],
                confidence_score=rf.confidence_score,
                method="evidence-reference",
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
            raw_fields=raw_fields,
            normalized_fields=normalized_fields,
            clinical_facts=clinical_facts,
        )
        # Completeness-based confidence so downstream UIs show something useful.
        if case.confidence_score <= 0.0:
            case.confidence_score = max(0.1, case.completeness)
        return case
