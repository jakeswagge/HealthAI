"""Canonical clinical fact contract shared across review workflows.

Clinical facts are the typed bridge between source-backed evidence, assertion
status, normalized state, conflict detection, review logic, and governance. The
existing ``EvidenceReference`` inventory remains the source citation layer; a
``ClinicalFact`` is the normalized clinical interpretation of one or more of
those evidence references.
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.models.evidence_reference import EvidenceReference


def new_clinical_fact_id() -> str:
    """Generate a stable-looking id for a normalized clinical fact."""
    return f"CF-{uuid.uuid4().hex[:12].upper()}"


class ClinicalFactDomain(str, Enum):
    DIAGNOSIS = "DIAGNOSIS"
    TB_SCREEN = "TB_SCREEN"
    STEP_THERAPY = "STEP_THERAPY"
    PROVIDER = "PROVIDER"
    CONTRAINDICATION = "CONTRAINDICATION"
    REQUESTED_SERVICE = "REQUESTED_SERVICE"
    DENIAL_REASON = "DENIAL_REASON"


class AssertionStatus(str, Enum):
    AFFIRMED = "AFFIRMED"
    NEGATED = "NEGATED"
    HISTORICAL = "HISTORICAL"
    FAMILY_HISTORY = "FAMILY_HISTORY"
    HYPOTHETICAL = "HYPOTHETICAL"
    UNCERTAIN = "UNCERTAIN"
    DIFFERENTIAL = "DIFFERENTIAL"


class TemporalityStatus(str, Enum):
    CURRENT = "CURRENT"
    PAST = "PAST"
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    UNKNOWN = "UNKNOWN"


class ConflictStatus(str, Enum):
    NONE = "NONE"
    CONFLICTED = "CONFLICTED"
    RESOLVED = "RESOLVED"


class GovernanceStatus(str, Enum):
    UNREVIEWED = "UNREVIEWED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FLAGGED = "FLAGGED"


class DiagnosisState(str, Enum):
    ACTIVE = "ACTIVE"
    NEGATED = "NEGATED"
    HISTORICAL = "HISTORICAL"
    POSSIBLE = "POSSIBLE"
    FAMILY_HISTORY = "FAMILY_HISTORY"
    RULE_OUT = "RULE_OUT"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class TBScreenState(str, Enum):
    NEGATIVE = "NEGATIVE"
    POSITIVE = "POSITIVE"
    PENDING = "PENDING"
    INDETERMINATE = "INDETERMINATE"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class StepTherapyState(str, Enum):
    FAILED = "FAILED"
    REFUSED = "REFUSED"
    CONTRAINDICATED = "CONTRAINDICATED"
    INTOLERANT = "INTOLERANT"
    IN_PROGRESS = "IN_PROGRESS"
    NEVER_STARTED = "NEVER_STARTED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class ProviderState(str, Enum):
    SPECIALIST = "SPECIALIST"
    NON_SPECIALIST = "NON_SPECIALIST"
    CONSULTING_SPECIALIST = "CONSULTING_SPECIALIST"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class ClinicalFact(BaseModel):
    """Typed, evidence-linked clinical fact used across the workflow."""

    fact_id: str = Field(default_factory=new_clinical_fact_id)
    case_id: str = ""
    domain: ClinicalFactDomain
    state: str = Field(default="UNKNOWN")
    value: str = ""
    assertion: AssertionStatus = AssertionStatus.AFFIRMED
    temporality: TemporalityStatus = TemporalityStatus.CURRENT
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    page_numbers: list[int] = Field(default_factory=list)
    quoted_text: str = ""
    conflict_status: ConflictStatus = ConflictStatus.NONE
    governance_status: GovernanceStatus = GovernanceStatus.UNREVIEWED
    created_by: str = "deterministic-normalizer"

    @field_validator("state", "value", "quoted_text", "created_by", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        return "" if v is None else str(v).strip()


def domain_for_fact_type(fact_type: str | None) -> ClinicalFactDomain | None:
    """Map legacy evidence fact types to the canonical fact domain."""
    fact = (fact_type or "").strip().lower()
    if fact in {"diagnosis", "diagnosis_assertion", "icd10_codes"}:
        return ClinicalFactDomain.DIAGNOSIS
    if fact in {"tb_screen_result", "criterion_tb_screen"}:
        return ClinicalFactDomain.TB_SCREEN
    if fact in {"step_therapy_status", "criterion_step_therapy"}:
        return ClinicalFactDomain.STEP_THERAPY
    if fact in {"provider_role", "specialist_status", "criterion_specialist"}:
        return ClinicalFactDomain.PROVIDER
    if fact == "requested_service":
        return ClinicalFactDomain.REQUESTED_SERVICE
    if fact in {"denial_reason", "claim_denial_reason"}:
        return ClinicalFactDomain.DENIAL_REASON
    return None


def clinical_fact_from_evidence(ref: EvidenceReference) -> ClinicalFact | None:
    """Build a clinical fact from a single source-backed evidence reference."""
    domain = domain_for_fact_type(ref.fact_type)
    if domain is None:
        return None
    value = _value_of(ref)
    state, assertion, temporality = _state_triplet(ref.fact_type or "", value, ref.quoted_text)
    return ClinicalFact(
        case_id=ref.case_id,
        domain=domain,
        state=state,
        value=_clean_value(value),
        assertion=assertion,
        temporality=temporality,
        confidence_score=ref.confidence_score,
        evidence_ids=[ref.evidence_id],
        source_document_ids=[ref.source_document_id],
        page_numbers=[ref.page_number],
        quoted_text=ref.quoted_text,
        created_by="evidence-reference",
    )


def clinical_facts_from_evidence(evidence: list[EvidenceReference]) -> list[ClinicalFact]:
    """Convert evidence references into canonical clinical facts."""
    facts: list[ClinicalFact] = []
    for ref in evidence:
        fact = clinical_fact_from_evidence(ref)
        if fact is not None:
            facts.append(fact)
    return facts


def fact_ids_for_evidence(
    facts: list[ClinicalFact],
    evidence_ids: list[str],
) -> list[str]:
    """Return clinical fact ids linked to any of the supplied evidence ids."""
    wanted = set(evidence_ids)
    ids: list[str] = []
    for fact in facts:
        if wanted.intersection(fact.evidence_ids) and fact.fact_id not in ids:
            ids.append(fact.fact_id)
    return ids


def _value_of(ref: EvidenceReference) -> str:
    if ": " in ref.normalized_fact:
        return ref.normalized_fact.split(": ", 1)[1]
    return ref.normalized_fact


def _clean_value(value: str) -> str:
    # Assertion references encode "value|STATE"; the clinical value is the
    # left-hand side.
    return value.split("|", 1)[0].strip()


def _state_triplet(
    fact_type: str,
    value: str,
    quote: str,
) -> tuple[str, AssertionStatus, TemporalityStatus]:
    fact = fact_type.lower()
    low = f"{value} {quote}".lower()

    if fact == "diagnosis":
        return DiagnosisState.ACTIVE.value, AssertionStatus.AFFIRMED, TemporalityStatus.CURRENT
    if fact == "diagnosis_assertion":
        token = value.split("|", 1)[1].strip().upper() if "|" in value else "UNKNOWN"
        if token == "NEGATED":
            return DiagnosisState.NEGATED.value, AssertionStatus.NEGATED, TemporalityStatus.CURRENT
        if token == "HISTORICAL":
            return DiagnosisState.HISTORICAL.value, AssertionStatus.HISTORICAL, TemporalityStatus.PAST
        if token == "FAMILY_HISTORY":
            return DiagnosisState.FAMILY_HISTORY.value, AssertionStatus.FAMILY_HISTORY, TemporalityStatus.PAST
        if token == "HYPOTHETICAL":
            return DiagnosisState.POSSIBLE.value, AssertionStatus.HYPOTHETICAL, TemporalityStatus.UNKNOWN
        if token == "DIFFERENTIAL":
            return DiagnosisState.RULE_OUT.value, AssertionStatus.DIFFERENTIAL, TemporalityStatus.UNKNOWN
        if token == "UNCERTAIN":
            return DiagnosisState.POSSIBLE.value, AssertionStatus.UNCERTAIN, TemporalityStatus.UNKNOWN
        return DiagnosisState.UNKNOWN.value, AssertionStatus.UNCERTAIN, TemporalityStatus.UNKNOWN

    if fact in {"tb_screen_result", "criterion_tb_screen"}:
        if "positive" in low or "reactive" in low or "detected" in low:
            return TBScreenState.POSITIVE.value, AssertionStatus.AFFIRMED, TemporalityStatus.CURRENT
        if "negative" in low or "nonreactive" in low or "non-reactive" in low or "not detected" in low:
            return TBScreenState.NEGATIVE.value, AssertionStatus.AFFIRMED, TemporalityStatus.CURRENT
        if "pending" in low or "awaiting" in low or "ordered" in low:
            return TBScreenState.PENDING.value, AssertionStatus.AFFIRMED, TemporalityStatus.PENDING
        if "indeterminate" in low or "equivocal" in low or "invalid" in low:
            return TBScreenState.INDETERMINATE.value, AssertionStatus.UNCERTAIN, TemporalityStatus.UNKNOWN
        if "absent" in low or "missing" in low or "not performed" in low or "not documented" in low:
            return TBScreenState.NOT_FOUND.value, AssertionStatus.NEGATED, TemporalityStatus.UNKNOWN
        return TBScreenState.UNKNOWN.value, AssertionStatus.UNCERTAIN, TemporalityStatus.UNKNOWN

    if fact in {"step_therapy_status", "criterion_step_therapy"}:
        if "never_started" in low or "never started" in low or "never initiated" in low:
            return StepTherapyState.NEVER_STARTED.value, AssertionStatus.NEGATED, TemporalityStatus.CURRENT
        if "refused" in low or "declined" in low or "non-compliant" in low or "noncompliant" in low:
            return StepTherapyState.REFUSED.value, AssertionStatus.NEGATED, TemporalityStatus.CURRENT
        if "in_progress" in low or "in progress" in low or "currently taking" in low:
            return StepTherapyState.IN_PROGRESS.value, AssertionStatus.AFFIRMED, TemporalityStatus.IN_PROGRESS
        if (
            "contraindicated" in low
            or "bypassed" in low
            or "renal failure" in low
            or "kidney failure" in low
            or "chronic kidney disease" in low
            or "stage 4 ckd" in low
            or "hepatic cirrhosis" in low
            or "cirrhosis" in low
        ):
            return StepTherapyState.CONTRAINDICATED.value, AssertionStatus.AFFIRMED, TemporalityStatus.CURRENT
        if "intoler" in low or "toxicity" in low:
            return StepTherapyState.INTOLERANT.value, AssertionStatus.AFFIRMED, TemporalityStatus.CURRENT
        if "failed" in low or "failure" in low or "methotrexate failure" in low:
            return StepTherapyState.FAILED.value, AssertionStatus.AFFIRMED, TemporalityStatus.PAST
        if "absent" in low or "not found" in low or "no methotrexate" in low:
            return StepTherapyState.NOT_FOUND.value, AssertionStatus.NEGATED, TemporalityStatus.UNKNOWN
        return StepTherapyState.UNKNOWN.value, AssertionStatus.UNCERTAIN, TemporalityStatus.UNKNOWN

    if fact in {"provider_role", "specialist_status", "criterion_specialist"}:
        if (
            "primary care" in low
            or "pcp" in low
            or "family physician" in low
            or "internist" in low
            or "chiropractic" in low
            or "chiropractor" in low
        ):
            return ProviderState.NON_SPECIALIST.value, AssertionStatus.AFFIRMED, TemporalityStatus.CURRENT
        if "consult" in low:
            return ProviderState.CONSULTING_SPECIALIST.value, AssertionStatus.AFFIRMED, TemporalityStatus.CURRENT
        if "specialist" in low or "rheum" in low or "derm" in low or "gastro" in low:
            return ProviderState.SPECIALIST.value, AssertionStatus.AFFIRMED, TemporalityStatus.CURRENT
        # Finding 3: Generic medical titles without specialty qualifiers
        # indicate a general provider, not an unknown provider.
        _GENERIC_TITLES = ("physician", " md", " do", " np ", " pa ", "nurse practitioner", "physician assistant")
        if any(title in low for title in _GENERIC_TITLES):
            return ProviderState.NON_SPECIALIST.value, AssertionStatus.AFFIRMED, TemporalityStatus.CURRENT
        return ProviderState.UNKNOWN.value, AssertionStatus.UNCERTAIN, TemporalityStatus.UNKNOWN

    return "DOCUMENTED", AssertionStatus.AFFIRMED, TemporalityStatus.CURRENT
