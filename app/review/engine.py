"""Deterministic clinical review engine.

Given a :class:`PatientCase` (and optionally the source document text as
supporting evidence), this engine:

1. Matches the case to the most relevant :class:`ClinicalGuideline`.
2. Evaluates each required criterion against the available evidence.
3. Detects contraindications.
4. Produces a validated :class:`ReviewResult` with a recommendation
   (APPROVE / DENY / INSUFFICIENT_INFORMATION), matched/missing criteria,
   missing evidence, recommended actions, rationale, and a confidence score.

Evidence model
--------------
The denial reason describes a *deficiency* (what the payer found lacking),
while the diagnosis, requested service, and document text provide *supporting*
evidence. A criterion is therefore:

- UNMET  if its keywords appear in the denial/deficiency text.
- MET    if its keywords appear in the supporting evidence (and it is not
         flagged as a deficiency).
- UNKNOWN if there is no evidence either way.

This engine is fully offline and independent of the extraction engine.
"""

from __future__ import annotations

import re

from app.agents.normalization import normalize_clinical_text
from app.guidelines.repository import (
    GuidelineRepository,
    get_default_repository,
)
from app.models.clinical_guideline import ClinicalGuideline, GuidelineCriterion
from app.models.clinical_fact import (
    ClinicalFact,
    ClinicalFactDomain,
    ConflictStatus,
    DiagnosisState,
    ProviderState,
    StepTherapyState,
    TBScreenState,
)
from app.models.patient_case import PatientCase
from app.models.review_result import (
    CriterionEvaluation,
    CriterionStatus,
    Recommendation,
    ReviewResult,
)
from app.policies.formulary import FormularyPolicyIndex, FormularyPolicyRule
from app.review.clinical_nlp import (
    ClinicalSignal,
    _STEP_ATTRIBUTION_CUES,
    _STEP_CONTRAINDICATION_CONDITION_CUES,
    canonical_diagnosis,
    extract_clinical_signals,
    step_therapy_status,
)


SPECIALIST_VOCABULARY = [
    "rheumatology",
    "evaluated by rheumatology",
    "rheumatology clinic",
    "rheumatology consultation",
    "specialist consultation",
    "specialist evaluation",
    "evaluated by specialist",
    "seen by specialist",
    "seen by rheumatology",
    "seen by rheumatologist",
    "under care of rheumatology",
    "referred to rheumatology",
    "board-certified rheumatologist",
    "consulting rheumatologist",
    "reviewed by rheumatology service",
    "rheum",
    "gastroenterology",
    "gastroenterologist",
    "gastroenterology consultation",
    "seen by gastroenterologist",
    "seen by gastroenterology",
    "referred to gastroenterology",
    "under care of gastroenterology",
]

TB_SCREEN_VOCABULARY = [
    "quantiferon",
    "quantiferon gold",
    "quantiferon-tb",
    "quantiferon-tb gold negative",
    "t-spot",
    "tb test negative",
    "tuberculosis screening negative",
    "tuberculosis test negative",
    "latent tb screening",
    "negative tb result",
]

STEP_THERAPY_VOCABULARY = [
    "failed methotrexate",
    "methotrexate trial",
    "inadequate response to methotrexate",
    "persistent symptoms despite methotrexate",
    "dmard failure",
    "conventional dmard failure",
    "methotrexate ineffective",
    "uncontrolled disease on methotrexate",
    "refractory to methotrexate",
    "methotrexate toxicity",
    "mtx toxicity",
    "methotrexate intolerance",
    "intolerant to methotrexate",
    "methotrexate-induced",
    "mtx-induced",
    "attributable to methotrexate",
    "attributable to mtx",
    "failed azathioprine",
    "azathioprine failure",
    "azathioprine trial",
    "trial of azathioprine",
    "trial of oral azathioprine",
    "inadequate response to azathioprine",
    "azathioprine ineffective",
    "refractory to azathioprine",
    "failed aza",
    "aza failure",
]

SYSTEMIC_THERAPY_VOCABULARY = [
    "failed systemic therapy",
    "systemic therapy failure",
    "systemic therapy failed",
    "failed phototherapy",
    "phototherapy failure",
]

_NEGATION_BEFORE_RE = re.compile(
    r"\b(no|not|without|absent|missing|lacks?|lack of|undocumented|refused|declined)\b"
    r"(?:\W+\w+){0,5}\W*$",
    re.IGNORECASE,
)
_NEGATION_AFTER_RE = re.compile(
    r"^\W*(?:\w+\W+){0,4}"
    r"\b(not documented|not performed|not available|was not performed|were not performed)\b",
    re.IGNORECASE,
)


def _keyword_pattern(keyword: str) -> re.Pattern:
    escaped = re.escape(keyword.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\-", r"[-\s]?")
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def _is_negated_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 200):start]
    after = text[end : min(len(text), end + 200)]
    return bool(_NEGATION_BEFORE_RE.search(before) or _NEGATION_AFTER_RE.search(after))


def _expanded_keywords(crit: GuidelineCriterion) -> list[str]:
    keywords = list(crit.keywords)
    marker = f"{crit.id} {crit.description}".lower()
    if "specialist" in marker or "rheumatologist" in marker:
        keywords.extend(SPECIALIST_VOCABULARY)
    if "tb_screen" in marker or "tuberculosis" in marker:
        keywords.extend(TB_SCREEN_VOCABULARY)
    if "step_therapy" in marker or "dmard" in marker or "methotrexate" in marker:
        keywords.extend(STEP_THERAPY_VOCABULARY)
    unique = list(dict.fromkeys(k for k in keywords if k.strip()))
    return sorted(unique, key=len, reverse=True)


def _contains_any(
    text: str,
    keywords: list[str],
    *,
    ignore_negated: bool = False,
) -> str | None:
    """Return the first deterministic keyword/phrase found in text, or None."""
    for kw in keywords:
        k = kw.strip()
        if not k:
            continue
        for match in _keyword_pattern(k).finditer(text):
            if ignore_negated and _is_negated_context(text, match.start(), match.end()):
                continue
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


_TB_NEGATIVE_CUES = (
    "negative",
    "nonreactive",
    "non-reactive",
    "not detected",
    "no evidence of",
    "no signs of",
    "without evidence of",
    "clearance",
    "verified negative",
)
_TB_POSITIVE_CUES = (
    "positive",
    "reactive",
    "detected",
    "active tb",
    "active tuberculosis",
    "latent tb infection",
    "latent tuberculosis infection",
)
_TB_ABSENCE_CUES = (
    "no tb screening",
    "no tuberculosis screening",
    "no tb test",
    "no tuberculosis test",
    "did not receive",
    "not received",
    "not provided",
    "not performed",
    "not documented",
    "documentation was not provided",
    "documentation not provided",
    "missing",
    "unavailable",
    "without tb screening",
    "without tuberculosis screening",
)
_TB_TEST_CUES = (
    "screen",
    "screening",
    "test",
    "result",
    "quantiferon",
    "t-spot",
    "ppd",
)
_STEP_SUCCESS_CUES = (
    "failed",
    "failure",
    "trial",
    "tried",
    "completed",
    "inadequate response",
    "persistent symptoms",
    "despite",
    "ineffective",
    "refractory",
    "uncontrolled",
    "intolerant",
    "intolerance",
    "contraindicated",
    "bypassed because",
    "bypassed due to",
)
_STEP_STRONG_SUCCESS_CUES = (
    "failed",
    "failure",
    "inadequate response",
    "persistent symptoms",
    "despite",
    "ineffective",
    "refractory",
    "uncontrolled",
    "intolerant",
    "intolerance",
)
_STEP_ABSENCE_CUES = (
    "no methotrexate",
    "no mtx",
    "no dmard",
    "not tried",
    "not trialed",
    "not documented",
    "missing",
    "without methotrexate",
    "without mtx",
    "without dmard",
    "refused",
    "refusal",
    "declined",
)
_STEP_REFUSAL_PATTERNS = (
    re.compile(
        r"\b(?:methotrexate|mtx|dmard)\b.{0,180}\b"
        r"(?:refus(?:ed|es|al)|declin(?:ed|es)|non[-\s]?compliant|"
        r"non[-\s]?adherent|never\s+(?:started|initiated)|"
        r"did\s+not\s+(?:start|initiate|fill|take|ingest)|"
        r"not\s+(?:started|initiated|filled|taken|ingested)|"
        r"would\s+not\s+(?:start|fill|take|ingest)|"
        r"fear(?:ful)?\s+of\s+side\s+effects|"
        r"afraid\s+of\s+side\s+effects|"
        r"concern(?:ed)?\s+about\s+side\s+effects)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:refus(?:ed|es|al)|declin(?:ed|es)|non[-\s]?compliant|"
        r"non[-\s]?adherent|never\s+(?:started|initiated)|"
        r"did\s+not\s+(?:start|initiate|fill|take|ingest)|"
        r"not\s+(?:started|initiated|filled|taken|ingested)|"
        r"would\s+not\s+(?:start|fill|take|ingest)|"
        r"fear(?:ful)?\s+of\s+side\s+effects|"
        r"afraid\s+of\s+side\s+effects|"
        r"concern(?:ed)?\s+about\s+side\s+effects)"
        r".{0,180}\b(?:methotrexate|mtx|dmard|prescription|medication|"
        r"therapy|treatment)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:methotrexate|mtx|dmard)\b.{0,220}\b"
        r"(?:direct\s+biologic|biologic\s+therapy|direct\s+biologic\s+therapy)",
        re.IGNORECASE | re.DOTALL,
    ),
)
_NON_DMARD_STEP_CONTEXT_CUES = (
    "topical steroid",
    "topical steroids",
    "steroid cream",
    "corticosteroid cream",
)
_NON_DMARD_SYSTEMIC_STEP_CUES = (
    "systemic therapy",
    "phototherapy",
)
_CONVENTIONAL_DMARD_LABELS = (
    ("methotrexate", ("methotrexate", "mtx")),
    ("azathioprine", ("azathioprine", "aza")),
    ("mercaptopurine", ("mercaptopurine", "6-mp")),
    ("thiopurine", ("thiopurine",)),
)
_SPECIALIST_ABSENCE_CUES = (
    "no specialist",
    "no rheumatologist",
    "no rheumatology",
    "no gastroenterologist",
    "no gastroenterology",
    "not documented",
    "missing",
    "without specialist",
    "without rheumatology",
    "without gastroenterology",
)
_SPECIALIST_CUES = (
    "rheumatologist",
    "rheumatology",
    "board certified rheumatologist",
    "board-certified rheumatologist",
    "prescriber is a board certified rheumatologist",
    "prescribed by a board certified rheumatologist",
    "rheumatologist prescribing",
    "rheumatology prescribing",
)
_NON_SPECIALIST_COORDINATOR_CUES = (
    "clinic coordinator",
    "care coordinator",
    "program coordinator",
    "office coordinator",
    "administrative coordinator",
    "referral coordinator",
    "scheduling coordinator",
)
_ARCHIVE_STALE_CUES = (
    "historical archive report",
    "generated from legacy",
    "legacy emerge system",
    "archive summary",
    "historical records",
)
_CURRENT_REQUEST_CUES = (
    "current correspondence",
    "current prior authorization",
    "current 2026 prior authorization",
    "approve the current",
)
_BIOLOGIC_PRIOR_CUES = (
    "previous",
    "previously",
    "prior",
    "past",
    "failed",
    "failure",
    "discontinued",
    "stopped",
    "history of",
)
_NEGATIVE_INFECTION_CUES = (
    "negative",
    "nonreactive",
    "non-reactive",
    "not detected",
    "no evidence of",
    "no signs of",
)
_EDUCATIONAL_CONDITION_CUES = (
    "conditions such as",
    "conditions like",
    "condition such as",
    "condition like",
    "approved for",
    "indicated for",
    "fda-approved",
    "covered under your plan",
    "covered for",
)
_POLICY_TEXT_CUES = (
    "policy requires",
    "clinical policy",
    "guideline requires",
    "must be",
    "prior to starting",
    "policy states",
)
_CLINICAL_KEYWORD_CUES = (
    "rheumatoid arthritis",
    "psoriatic arthritis",
    "plaque psoriasis",
    "crohn",
    "ulcerative colitis",
    "tuberculosis",
    "tb",
    "tb screen",
    "tb screening",
    "humira",
    "adalimumab",
    "enbrel",
    "etanercept",
    "methotrexate",
)
_TB_MISSING_DOCUMENTATION_RE = re.compile(
    r"(?:"
    r"(?:did\s+not\s+receive|not\s+received|not\s+provided|not\s+documented|missing)"
    r".{0,100}\b(?:tb|tuberculosis)\b.{0,60}\b(?:screen|screening|test|documentation|result)\b"
    r"|"
    r"\b(?:tb|tuberculosis)\b.{0,60}\b(?:screen|screening|test|documentation|result)\b"
    r".{0,100}(?:did\s+not\s+receive|not\s+received|not\s+provided|not\s+documented|missing)"
    r"|"
    r"\brequires?\b.{0,80}\bnegative\b.{0,40}\b(?:tb|tuberculosis)\b"
    r".{0,60}\b(?:screen|screening|test)\b.{0,160}"
    r"(?:documentation\s+(?:was\s+)?not\s+provided|not\s+provided|missing)"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_NONCOVERED_OSTEOARTHRITIS_RE = re.compile(
    r"(?:"
    r"\bhumira\b.{0,120}\bnot\s+indicated\b.{0,120}\bosteoarthritis\b"
    r"|"
    r"\bosteoarthritis\b.{0,120}\bhumira\b.{0,120}\bnot\s+indicated\b"
    r"|"
    r"\bdiagnosis\s+of\s+osteoarthritis\b.{0,160}"
    r"\bnot\s+(?:indicated|considered\s+medically\s+necessary)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_PSORIASIS_SEVERITY_MISSING_RE = re.compile(
    r"\b(?:plaque\s+psoriasis|psoriasis)\b.{0,260}"
    r"\b(?:lacks?|missing|not\s+established|not\s+documented|without)\b"
    r".{0,160}\b(?:bsa|body\s+surface\s+area|pasi|severity\s+metrics?)\b"
    r"|"
    r"\b(?:bsa|body\s+surface\s+area|pasi|severity\s+metrics?)\b.{0,180}"
    r"\b(?:lacks?|missing|not\s+established|not\s+documented|without)\b",
    re.IGNORECASE | re.DOTALL,
)
_DIFFERENTIAL_DIAGNOSIS_RE = re.compile(
    r"\b(?:differential\s+diagnos(?:is|es)|possible|suspected)"
    r"\b.{0,180}\b(?:rheumatoid\s+arthritis|ra)\b"
    r"|"
    r"\b(?:rheumatoid\s+arthritis|ra)\b.{0,80}\bvs\.?\b.{0,120}"
    r"|"
    r"\bserology\s+(?:is\s+)?(?:currently\s+)?pending\b",
    re.IGNORECASE | re.DOTALL,
)
_RULE_OUT_RA_RE = re.compile(
    r"\b(?:rule\s*out|r/o)\b.{0,80}\b(?:rheumatoid\s+arthritis|ra)\b",
    re.IGNORECASE | re.DOTALL,
)


def _low(value: str) -> str:
    return value.lower()


def _has_any(value: str, cues: tuple[str, ...]) -> bool:
    low = _low(value)
    return any(cue in low for cue in cues)


def _is_educational_text(value: str) -> bool:
    low = _low(value)
    if not _has_any(low, _CLINICAL_KEYWORD_CUES):
        return False
    return _has_any(low, _EDUCATIONAL_CONDITION_CUES) or _has_any(
        low, _POLICY_TEXT_CUES
    )


def _remove_educational_text(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    kept = [part for part in parts if not _is_educational_text(part)]
    return " \n ".join(kept)


def _has_missing_tb_documentation(text: str) -> bool:
    return bool(_TB_MISSING_DOCUMENTATION_RE.search(text))


def _has_noncovered_osteoarthritis_indication(text: str) -> bool:
    return bool(_NONCOVERED_OSTEOARTHRITIS_RE.search(text))


def _has_missing_psoriasis_severity_metrics(text: str) -> bool:
    return bool(_PSORIASIS_SEVERITY_MISSING_RE.search(text))


def _has_differential_or_pending_ra(text: str) -> bool:
    return bool(_DIFFERENTIAL_DIAGNOSIS_RE.search(text))


def _has_rule_out_ra(text: str) -> bool:
    return bool(_RULE_OUT_RA_RE.search(text))


def _has_stale_archive_for_current_request(text: str) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in _ARCHIVE_STALE_CUES) and any(
        cue in low for cue in _CURRENT_REQUEST_CUES
    )


def _is_non_specialist_coordinator_text(text: str) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in _NON_SPECIALIST_COORDINATOR_CUES)


def _has_step_therapy_refusal(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _STEP_REFUSAL_PATTERNS)


def _is_non_dmard_step_text(text: str) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in _NON_DMARD_STEP_CONTEXT_CUES) and not any(
        cue in low
        for cue in ("methotrexate", "mtx", "dmard", "azathioprine", "aza")
    )


def _allows_systemic_step_therapy(crit: GuidelineCriterion) -> bool:
    text = " ".join([crit.description, *crit.keywords]).lower()
    return "systemic therapy" in text or "phototherapy" in text


def _is_systemic_step_failure_text(text: str) -> bool:
    low = (text or "").lower()
    if not any(cue in low for cue in _NON_DMARD_SYSTEMIC_STEP_CUES):
        return False
    return any(cue in low for cue in _STEP_STRONG_SUCCESS_CUES)


def _step_therapy_failure_phrase(text: str) -> str:
    low = (text or "").lower()
    for label, cues in _CONVENTIONAL_DMARD_LABELS:
        if any(cue in low for cue in cues):
            return f"{label} failure"
    if "dmard" in low:
        return "conventional DMARD failure"
    return "step therapy failure"


def _signals(signals: list[ClinicalSignal], label: str) -> list[ClinicalSignal]:
    return [s for s in signals if s.label == label]


def _signals_any(
    signals: list[ClinicalSignal],
    labels: tuple[str, ...],
) -> list[ClinicalSignal]:
    return [s for s in signals if s.label in labels]


def _signal_note(prefix: str, signal: ClinicalSignal) -> str:
    flags = []
    if signal.is_negated:
        flags.append("negated")
    if signal.is_historical:
        flags.append("historical")
    if signal.is_hypothetical:
        flags.append("hypothetical")
    if signal.is_uncertain:
        flags.append("uncertain")
    suffix = f"; context={','.join(flags)}" if flags else ""
    evidence = re.sub(r"\s+", " ", signal.sentence).strip()
    return f"{prefix} ('{signal.text}'{suffix}; evidence='{evidence}')."


def _normalized_value(case: PatientCase, fact_type: str) -> str | None:
    field = (case.normalized_fields or {}).get(fact_type)
    if field is None:
        return None
    value = field.normalized_value or field.raw_value
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_support_text(case: PatientCase) -> str:
    """Convert normalized evidence facts into review-friendly text phrases."""
    parts: list[str] = []
    for fact in ("diagnosis", "requested_service", "provider_role"):
        value = _normalized_value(case, fact)
        if value:
            parts.append(value)

    tb_result = _normalized_value(case, "tb_screen_result")
    if tb_result:
        parts.append(f"{tb_result} TB screen result")
        if tb_result.lower() == "negative":
            parts.append("negative TB screening")
        elif tb_result.lower() == "positive":
            parts.append("positive TB screening")

    specialist = _normalized_value(case, "specialist_status")
    if specialist and specialist.lower() == "documented":
        parts.extend(("specialist documented", "rheumatology specialist"))

    step = _normalized_value(case, "step_therapy_status")
    if step:
        parts.append(f"step therapy status {step}")
        if step.lower() == "failed":
            phrase = _step_therapy_failure_phrase(
                " ".join(
                    fact.value
                    for fact in case.clinical_facts or []
                    if fact.domain == ClinicalFactDomain.STEP_THERAPY
                    and fact.state == StepTherapyState.FAILED.value
                )
            )
            parts.extend((phrase.replace(" failure", " failed"), phrase))
        elif step.lower() in ("intolerance", "intolerant", "toxicity"):
            phrase = _step_therapy_failure_phrase(
                " ".join(
                    fact.value
                    for fact in case.clinical_facts or []
                    if fact.domain == ClinicalFactDomain.STEP_THERAPY
                    and fact.state in {
                        StepTherapyState.INTOLERANT.value,
                        StepTherapyState.CONTRAINDICATED.value,
                    }
                )
            )
            drug = phrase.replace(" failure", "")
            parts.extend((f"{drug} intolerance", f"intolerant to {drug}"))
        elif step.lower() == "refused":
            drug = _step_therapy_failure_phrase(
                " ".join(
                    fact.value
                    for fact in case.clinical_facts or []
                    if fact.domain == ClinicalFactDomain.STEP_THERAPY
                )
            ).replace(" failure", "")
            parts.append(f"{drug} refused")
        elif step.lower() == "absent":
            parts.append("no DMARD trial")

    for fact in case.clinical_facts or []:
        if fact.domain == ClinicalFactDomain.DIAGNOSIS:
            if fact.state == DiagnosisState.ACTIVE.value:
                parts.append(fact.value)
            else:
                parts.append(f"diagnosis {fact.value} state {fact.state}")
        elif fact.domain == ClinicalFactDomain.TB_SCREEN:
            parts.append(f"tb screen state {fact.state} {fact.value}")
        elif fact.domain == ClinicalFactDomain.STEP_THERAPY:
            parts.append(f"step therapy state {fact.state} {fact.value}")
        elif fact.domain == ClinicalFactDomain.PROVIDER:
            parts.append(f"provider state {fact.state} {fact.value}")

    return " \n ".join(parts)


def _normalized_deficiency_text(case: PatientCase) -> str:
    parts: list[str] = []
    prior_auth = _normalized_value(case, "prior_auth_status")
    if prior_auth and prior_auth.lower() == "missing":
        parts.append("missing prior authorization")
    claim_reason = _normalized_value(case, "claim_denial_reason")
    if claim_reason:
        parts.append(claim_reason)
    return " \n ".join(parts)


def _source_ids_for(case: PatientCase, fact_types: tuple[str, ...]) -> list[str]:
    ids: list[str] = []
    for fact in fact_types:
        source = (case.field_sources or {}).get(fact)
        if source and source.evidence_id and source.evidence_id not in ids:
            ids.append(source.evidence_id)
        normalized = (case.normalized_fields or {}).get(fact)
        if normalized:
            for ev_id in normalized.source_evidence_ids:
                if ev_id not in ids:
                    ids.append(ev_id)
    return ids



def _evidence_ids_for_criterion(case: PatientCase, crit: GuidelineCriterion) -> list[str]:
    marker = f"{crit.id} {crit.description}".lower()
    facts: list[str] = []
    if "diagnosis" in marker or "rheumatoid arthritis" in marker:
        facts.append("diagnosis")
    if "specialist" in marker or "rheumatologist" in marker:
        facts.extend(("criterion_specialist", "specialist_status", "provider_role"))
    if "tb_screen" in marker or "tuberculosis" in marker:
        facts.extend(("tb_screen_result", "criterion_tb_screen"))
    if "dmard" in marker or "methotrexate" in marker or "step_therapy" in marker:
        facts.extend(("criterion_step_therapy", "step_therapy_status"))
    return _source_ids_for(case, tuple(facts))


def _fact_evidence_ids(facts: list[ClinicalFact]) -> list[str]:
    ids: list[str] = []
    for fact in facts:
        for ev_id in fact.evidence_ids:
            if ev_id not in ids:
                ids.append(ev_id)
    return ids


def _facts_for_domain(
    case: PatientCase,
    domain: ClinicalFactDomain,
) -> list[ClinicalFact]:
    return [f for f in case.clinical_facts or [] if f.domain == domain]


def _criterion_facts(
    case: PatientCase,
    crit: GuidelineCriterion,
) -> list[ClinicalFact]:
    marker = f"{crit.id} {crit.description}".lower()
    domains: list[ClinicalFactDomain] = []
    if "diagnosis" in marker or "rheumatoid arthritis" in marker:
        domains.append(ClinicalFactDomain.DIAGNOSIS)
    if "tb_screen" in marker or "tuberculosis" in marker:
        domains.append(ClinicalFactDomain.TB_SCREEN)
    if "dmard" in marker or "methotrexate" in marker or "step_therapy" in marker:
        domains.append(ClinicalFactDomain.STEP_THERAPY)
    if "specialist" in marker or "rheumatologist" in marker:
        domains.append(ClinicalFactDomain.PROVIDER)
    return [
        fact for fact in case.clinical_facts or []
        if fact.domain in domains
    ]


def _evaluate_with_clinical_facts(
    crit: GuidelineCriterion,
    case: PatientCase,
    *,
    support_text: str = "",
) -> tuple[str, str | None, list[str]] | None:
    """Evaluate a criterion from canonical clinical facts when possible."""
    marker = f"{crit.id} {crit.description}".lower()

    if "tb_screen" in marker or "tuberculosis" in marker:
        facts = _facts_for_domain(case, ClinicalFactDomain.TB_SCREEN)
        conflicted = [f for f in facts if f.conflict_status is ConflictStatus.CONFLICTED]
        if conflicted:
            return "unknown", "Conflicting TB screening evidence requires human review.", _fact_evidence_ids(conflicted)
        positive = [f for f in facts if f.state == TBScreenState.POSITIVE.value]
        if positive:
            return "unmet", "Positive TB screening evidence found.", _fact_evidence_ids(positive)
        missing = [
            f for f in facts
            if f.state
            in {
                TBScreenState.NOT_FOUND.value,
                TBScreenState.PENDING.value,
                TBScreenState.INDETERMINATE.value,
                TBScreenState.UNKNOWN.value,
            }
        ]
        if missing:
            return "unmet", "Negative TB screening is not established.", _fact_evidence_ids(missing)
        negative = [f for f in facts if f.state == TBScreenState.NEGATIVE.value]
        if negative:
            return "met", "Negative TB screening evidence found.", _fact_evidence_ids(negative)

    if "dmard" in marker or "methotrexate" in marker or "step_therapy" in marker:
        facts = _facts_for_domain(case, ClinicalFactDomain.STEP_THERAPY)
        conflicted = [f for f in facts if f.conflict_status is ConflictStatus.CONFLICTED]
        if conflicted:
            return "unknown", "Conflicting step therapy evidence requires human review.", _fact_evidence_ids(conflicted)
        refused = [
            f for f in facts
            if f.state in {
                StepTherapyState.REFUSED.value,
                StepTherapyState.NEVER_STARTED.value,
            }
        ]
        if refused:
            return "unmet", "Step therapy refusal/non-initiation documented.", _fact_evidence_ids(refused)
        absent = [f for f in facts if f.state == StepTherapyState.NOT_FOUND.value]
        if absent:
            return "unmet", "Step therapy absence documented.", _fact_evidence_ids(absent)
        in_progress = [f for f in facts if f.state == StepTherapyState.IN_PROGRESS.value]
        if in_progress:
            return "unknown", "Step therapy is documented as in progress, not failed.", _fact_evidence_ids(in_progress)
        exceptions = [
            f for f in facts
            if f.state in {
                StepTherapyState.INTOLERANT.value,
                StepTherapyState.CONTRAINDICATED.value,
            }
        ]
        if exceptions:
            return "met", "Step therapy satisfied via intolerance/contraindication exception.", _fact_evidence_ids(exceptions)
        failed = [f for f in facts if f.state == StepTherapyState.FAILED.value]
        if failed:
            return "met", "Step therapy failure evidence found.", _fact_evidence_ids(failed)
        if _allows_systemic_step_therapy(crit):
            systemic = [
                f
                for f in facts
                if f.state == StepTherapyState.UNKNOWN.value
                and _is_systemic_step_failure_text(f.quoted_text or f.value)
            ]
            if systemic:
                return "met", "Systemic or phototherapy step evidence found.", _fact_evidence_ids(systemic)

    if "specialist" in marker or "rheumatologist" in marker:
        facts = _facts_for_domain(case, ClinicalFactDomain.PROVIDER)
        direct = [
            f for f in facts
            if f.state in {
                ProviderState.SPECIALIST.value,
                ProviderState.CONSULTING_SPECIALIST.value,
            }
        ]
        non = [f for f in facts if f.state == ProviderState.NON_SPECIALIST.value]
        if direct and non:
            all_facts = [*direct, *non]
            return "unknown", "Conflicting specialist/non-specialist evidence requires human review.", _fact_evidence_ids(all_facts)
        if direct:
            prescriber = [
                f for f in direct
                if "prescriber" in f.quoted_text.lower()
                or "requesting provider" in f.quoted_text.lower()
                or "ordering provider" in f.quoted_text.lower()
            ]
            chosen = prescriber or direct
            quote = re.sub(r"\s+", " ", chosen[0].quoted_text).strip()
            if quote:
                return "met", f"Specialist evidence found ('{quote}').", _fact_evidence_ids(chosen)
            return "met", "Specialist prescriber/consultation evidence found.", _fact_evidence_ids(chosen)
        if non:
            return "unmet", "Provider evidence does not establish an appropriate specialist.", _fact_evidence_ids(non)

    if "diagnosis" in marker or "rheumatoid arthritis" in marker:
        if _has_rule_out_ra(support_text):
            return "unmet", "Rheumatoid arthritis is not established because the diagnosis is being ruled out.", []
        if _has_differential_or_pending_ra(support_text):
            return "unknown", "Diagnosis is differential or pending and requires human review.", []
        if _has_missing_psoriasis_severity_metrics(support_text):
            return "unmet", "Plaque psoriasis severity metrics (BSA or PASI score) are not established.", []
        facts = _facts_for_domain(case, ClinicalFactDomain.DIAGNOSIS)
        conflicted = [f for f in facts if f.conflict_status is ConflictStatus.CONFLICTED]
        if conflicted:
            return "unknown", "Conflicting diagnosis evidence requires human review.", _fact_evidence_ids(conflicted)
        active_ra = [
            f for f in facts
            if f.state == DiagnosisState.ACTIVE.value
            and "rheumatoid arthritis" in f.value.lower()
            and f.conflict_status is not ConflictStatus.CONFLICTED
        ]
        if active_ra:
            return "met", "Active rheumatoid arthritis diagnosis evidence found.", _fact_evidence_ids(active_ra)
        non_active_ra = [
            f for f in facts
            if "rheumatoid arthritis" in f.value.lower()
            and f.state != DiagnosisState.ACTIVE.value
        ]
        if non_active_ra:
            return "unmet", "Rheumatoid arthritis is not documented as an active diagnosis.", _fact_evidence_ids(non_active_ra)

    return None


def _clinical_contraindications(case: PatientCase) -> list[str]:
    found: list[str] = []
    tb_positive = [
        f for f in _facts_for_domain(case, ClinicalFactDomain.TB_SCREEN)
        if f.state == TBScreenState.POSITIVE.value
    ]
    if tb_positive:
        found.append("Positive tuberculosis (TB) evidence detected.")
    return found


def _clinical_conflict_reasons(case: PatientCase) -> list[str]:
    domains: dict[str, int] = {}
    for fact in case.clinical_facts or []:
        if fact.conflict_status is ConflictStatus.CONFLICTED:
            domains[fact.domain.value] = domains.get(fact.domain.value, 0) + 1
    return [
        f"Unresolved clinical fact conflict in {domain} ({count} fact(s))."
        for domain, count in sorted(domains.items())
    ]


def _criterion_status(status: str) -> CriterionStatus:
    if status == "met":
        return CriterionStatus.MET
    if status == "unmet":
        return CriterionStatus.NOT_MET
    return CriterionStatus.UNKNOWN


def _criterion_confidence(status: str, evidence_ids: list[str]) -> float:
    if status == "met":
        return 0.9 if evidence_ids else 0.82
    if status == "unmet":
        return 0.82
    return 0.5


def _current_diagnosis_values(
    signals: list[ClinicalSignal],
) -> list[tuple[str, ClinicalSignal]]:
    values: list[tuple[str, ClinicalSignal]] = []
    seen: set[str] = set()
    for signal in signals:
        if not signal.label.startswith("DIAGNOSIS_"):
            continue
        canonical = canonical_diagnosis(signal)
        if canonical is None or canonical in seen:
            continue
        seen.add(canonical)
        values.append((canonical, signal))
    return values


def _is_tb_absence(signal: ClinicalSignal) -> bool:
    return _has_any(signal.sentence, _TB_ABSENCE_CUES)


def _is_tb_negative_screen(signal: ClinicalSignal) -> bool:
    if signal.is_negated or _is_tb_absence(signal):
        return False
    return _has_any(signal.sentence, _TB_NEGATIVE_CUES)


def _is_tb_positive(signal: ClinicalSignal) -> bool:
    if signal.is_negated or signal.is_historical or signal.is_hypothetical:
        return False
    if _is_tb_absence(signal) or _is_tb_negative_screen(signal):
        return False
    if _has_any(signal.sentence, _TB_POSITIVE_CUES):
        return True
    return False


def _is_tb_screening_documented(signal: ClinicalSignal) -> bool:
    if signal.is_negated or _is_tb_absence(signal) or _is_tb_positive(signal):
        return False
    return _has_any(signal.text, _TB_TEST_CUES) or _has_any(signal.sentence, _TB_TEST_CUES)


def _is_current_biologic(signal: ClinicalSignal) -> bool:
    if not signal.is_current_affirmed:
        return False
    return not _has_any(signal.sentence, _BIOLOGIC_PRIOR_CUES)


def _medspacy_contraindications(
    support_signals: list[ClinicalSignal],
    deficiency_signals: list[ClinicalSignal],
) -> list[str]:
    signals = [*support_signals, *deficiency_signals]
    found: list[str] = []

    if any(_is_tb_positive(s) for s in _signals(signals, "TB")):
        found.append("Positive tuberculosis (TB) evidence detected.")

    hep_b = _signals(signals, "HEP_B")
    if any(
        s.is_current_affirmed and not _has_any(s.sentence, _NEGATIVE_INFECTION_CUES)
        for s in hep_b
    ):
        found.append("Hepatitis B evidence detected.")

    humira_current = any(_is_current_biologic(s) for s in _signals(signals, "BIOLOGIC_HUMIRA"))
    enbrel_current = any(_is_current_biologic(s) for s in _signals(signals, "BIOLOGIC_ENBREL"))
    if humira_current and enbrel_current:
        found.append("Concurrent biologic therapy detected (Humira and Enbrel).")

    return found


def _evaluate_with_medspacy(
    crit: GuidelineCriterion,
    support_signals: list[ClinicalSignal],
    deficiency_signals: list[ClinicalSignal],
) -> tuple[str, str | None] | None:
    marker = f"{crit.id} {crit.description}".lower()

    if "tb_screen" in marker or "tuberculosis" in marker:
        tb_def = _signals(deficiency_signals, "TB")
        if tb_def:
            return "unmet", _signal_note("Denial references TB screening", tb_def[0])

        support_text = " ".join(s.sentence for s in support_signals)
        if _has_missing_tb_documentation(support_text):
            return "unmet", "TB screening documentation is stated as missing or not received."

        tb_support = _signals(support_signals, "TB")
        if tb_support and _has_stale_archive_for_current_request(support_text):
            return (
                "unknown",
                "TB screening is documented only in stale historical archive records for a current request.",
            )
        positive = next((s for s in tb_support if _is_tb_positive(s)), None)
        if positive:
            return "unmet", _signal_note("Positive TB evidence found", positive)
        absent = next((s for s in tb_support if _is_tb_absence(s)), None)
        if absent:
            return "unmet", _signal_note("TB screening absence documented", absent)
        negative = next((s for s in tb_support if _is_tb_negative_screen(s)), None)
        if negative:
            return "met", _signal_note("Negative TB screening evidence found", negative)
        documented = next((s for s in tb_support if _is_tb_screening_documented(s)), None)
        if documented:
            return "met", _signal_note("TB screening evidence found", documented)

    if "dmard" in marker or "methotrexate" in marker or "step_therapy" in marker:
        step_def = _signals(deficiency_signals, "STEP_THERAPY")
        if step_def:
            return "unmet", _signal_note("Denial references step therapy", step_def[0])

        step_support = _signals(support_signals, "STEP_THERAPY")
        refused = next(
            (s for s in step_support if step_therapy_status(s) == "refused"),
            None,
        )
        intolerance = next(
            (s for s in step_support if step_therapy_status(s) == "intolerance"),
            None,
        )
        absent = next(
            (
                s for s in step_support
                if not _has_any(s.sentence, _STEP_STRONG_SUCCESS_CUES)
                and (
                    s.is_negated
                    or _has_any(s.sentence, _STEP_ABSENCE_CUES)
                    or step_therapy_status(s) == "absent"
                )
            ),
            None,
        )
        met = next(
            (
                s for s in step_support
                if (
                    (
                        step_therapy_status(s) == "failed"
                        or _has_any(s.sentence, _STEP_STRONG_SUCCESS_CUES)
                    )
                    and not _has_any(s.sentence, _STEP_ABSENCE_CUES)
                )
                or (
                    s.is_current_affirmed
                    and _has_any(s.sentence, _STEP_SUCCESS_CUES)
                    and not _has_any(s.sentence, _STEP_ABSENCE_CUES)
                )
            ),
            None,
        )
        medical_exception = next(
            (
                s for s in step_support
                if (
                    _has_any(s.sentence, _STEP_CONTRAINDICATION_CONDITION_CUES)
                    and _has_any(s.sentence, _STEP_ATTRIBUTION_CUES)
                )
                or _has_any(
                    s.sentence,
                    ("medical exception", "human specialist review", "human review"),
                )
            ),
            None,
        )
        if refused:
            return "unmet", _signal_note("Step therapy refusal documented", refused)
        if medical_exception:
            return "unmet", _signal_note(
                "Medical exception or contraindication requires human review",
                medical_exception,
            )
        systemic = next(
            (
                s for s in step_support
                if _is_systemic_step_failure_text(s.sentence)
                and _allows_systemic_step_therapy(crit)
            ),
            None,
        )
        if systemic:
            return "met", _signal_note("Systemic or phototherapy step evidence found", systemic)
        if any(_is_non_dmard_step_text(s.sentence) for s in step_support):
            return "unmet", "Only non-DMARD therapy failure is documented; conventional DMARD step therapy is not established."
        if intolerance:
            return "met", _signal_note(
                "Step therapy satisfied via intolerance/toxicity exception",
                intolerance,
            )
        if absent:
            return "unmet", _signal_note("Step therapy absence documented", absent)
        if met:
            return "met", _signal_note("Step therapy evidence found", met)
        if step_support:
            return "unknown", _signal_note(
                "Step therapy mention lacks trial/failure status",
                step_support[0],
            )

    if "specialist" in marker or "rheumatologist" in marker:
        specialist_labels = ("SPECIALIST_RHEUM", "SPECIALIST_DERM", "SPECIALIST_GI")
        non_specialist_labels = ("PROVIDER_PRIMARY_CARE", "PROVIDER_CHIROPRACTIC")
        specialist_def = _signals_any(deficiency_signals, specialist_labels)
        if specialist_def and not any(
            cue in specialist_def[0].sentence.lower()
            for cue in ("human specialist review", "medical exception")
        ):
            return "unmet", _signal_note("Denial references specialist involvement", specialist_def[0])

        specialist_support = _signals_any(support_signals, specialist_labels)
        non_specialist_support = _signals_any(support_signals, non_specialist_labels)
        coordinator = next(
            (
                s for s in specialist_support
                if s.is_current_affirmed
                and _is_non_specialist_coordinator_text(s.sentence)
            ),
            None,
        )
        if coordinator:
            return "unknown", _signal_note(
                "Coordinator title does not establish specialist prescribing or consultation",
                coordinator,
            )
        absent = next(
            (
                s for s in specialist_support
                if s.is_negated or _has_any(s.sentence, _SPECIALIST_ABSENCE_CUES)
            ),
            None,
        )
        met = next((s for s in specialist_support if s.is_current_affirmed), None)
        if not met:
            met = next(
                (
                    s for s in support_signals
                    if _has_any(s.sentence, _SPECIALIST_CUES)
                    and not _is_non_specialist_coordinator_text(s.sentence)
                    and not s.is_negated
                    and not _has_any(s.sentence, _SPECIALIST_ABSENCE_CUES)
                ),
                None,
            )
        if met:
            return "met", _signal_note("Specialist evidence found", met)
        if absent:
            return "unmet", _signal_note("Specialist absence documented", absent)
        non_specialist = next(
            (s for s in non_specialist_support if s.is_current_affirmed),
            None,
        )
        if non_specialist:
            return "unmet", _signal_note(
                "Provider evidence does not establish an appropriate specialist",
                non_specialist,
            )

    if "diagnosis" in marker or "rheumatoid arthritis" in marker:
        support_sentences = " ".join(s.sentence for s in support_signals)
        if _has_rule_out_ra(support_sentences):
            return "unmet", "Rheumatoid arthritis is not established because the diagnosis is being ruled out."
        if _has_differential_or_pending_ra(support_sentences):
            return "unknown", "Diagnosis is differential or pending and requires human review."
        ra_def = _signals(deficiency_signals, "DIAGNOSIS_RA")
        if ra_def:
            return "unmet", _signal_note("Denial references diagnosis", ra_def[0])

        current_diagnoses = _current_diagnosis_values(support_signals)
        if len(current_diagnoses) > 1:
            values = "; ".join(value for value, _ in current_diagnoses)
            return (
                "unknown",
                f"Conflicting current diagnoses require human review ({values}).",
            )

        ra_support = _signals(support_signals, "DIAGNOSIS_RA")
        met = next((s for s in ra_support if s.is_current_affirmed), None)
        if met:
            return "met", _signal_note("Diagnosis evidence found", met)
        not_met = next(
            (
                s for s in ra_support
                if s.is_negated or s.is_uncertain or s.is_hypothetical
            ),
            None,
        )
        if not_met:
            return "unmet", _signal_note("Diagnosis is not established", not_met)

    return None


class ClinicalReviewEngine:
    """Rule-based clinical guideline review."""

    def __init__(
        self,
        repository: GuidelineRepository | None = None,
        *,
        formulary_policy: FormularyPolicyIndex | None = None,
        payer_id: str | None = None,
    ):
        self.repository = repository or get_default_repository()
        self.formulary_policy = formulary_policy
        self.payer_id = payer_id

    # ------------------------------------------------------------------ #
    # Evidence assembly
    # ------------------------------------------------------------------ #
    @staticmethod
    def _support_text(case: PatientCase, document_text: str | None) -> str:
        parts = [
            case.diagnosis or "",
            case.requested_service or "",
            " ".join(case.icd10_codes),
            " ".join(case.cpt_codes),
            normalize_clinical_text(document_text),
            _normalized_support_text(case),
        ]
        return " \n ".join(parts).lower()

    @staticmethod
    def _deficiency_text(case: PatientCase, document_text: str | None) -> str:
        # The denial reason is the primary deficiency signal. We do NOT fold in
        # the full document here so that supporting evidence is not mistaken for
        # a deficiency.
        parts = [
            normalize_clinical_text(case.denial_reason),
            _normalized_deficiency_text(case),
        ]
        return " \n ".join(parts).lower()

    # ------------------------------------------------------------------ #
    # Core review
    # ------------------------------------------------------------------ #
    def review(
        self,
        case: PatientCase,
        document_text: str | None = None,
    ) -> ReviewResult:
        """Review a patient case against the matched clinical guideline."""
        match = self.repository.match(case)

        if match is None:
            return self._no_guideline_result(case)

        guideline = match.guideline
        raw_support = self._support_text(case, document_text)
        support = _remove_educational_text(raw_support)
        deficiency = self._deficiency_text(case, document_text)
        support_signals = extract_clinical_signals(support)
        deficiency_signals = extract_clinical_signals(deficiency)
        formulary_rule = self._formulary_rule_for_case(case, guideline)

        matched_criteria: list[str] = []
        missing_criteria: list[str] = []
        missing_evidence: list[str] = []
        unknown_criteria: list[str] = []
        detail: list[CriterionEvaluation] = []

        for crit in guideline.required_criteria:
            policy_status = self._evaluate_with_policy(crit, formulary_rule)
            fact_status = None
            fact_evidence_ids: list[str] = []
            if policy_status is not None:
                status, note = policy_status
            else:
                fact_status = _evaluate_with_clinical_facts(
                    crit,
                    case,
                    support_text=raw_support,
                )
                if fact_status is not None:
                    status, note, fact_evidence_ids = fact_status
                else:
                    status, note = self._evaluate_criterion(
                        crit,
                        support,
                        deficiency,
                        raw_support=raw_support,
                        support_signals=support_signals,
                        deficiency_signals=deficiency_signals,
                    )
            criterion_evidence_ids = fact_evidence_ids or _evidence_ids_for_criterion(
                case, crit
            )
            if policy_status is not None and formulary_rule is not None:
                policy_meta = {
                    "payer_id": formulary_rule.payer_id,
                    "guideline_pack": formulary_rule.guideline_pack,
                    "drug_key": formulary_rule.drug_key,
                }
                note = f"{note} Policy={policy_meta}."
                fact_evidence_ids = [
                    f"policy:{formulary_rule.source_resource_id or formulary_rule.drug_key}"
                ]
                criterion_evidence_ids = fact_evidence_ids
            supporting_evidence_ids = criterion_evidence_ids
            not_met_evidence_ids = (
                criterion_evidence_ids if status == "unmet" else []
            )
            rule_missing = [] if status == "met" else [
                (
                    f"Evidence for: {crit.description}"
                    if status == "unmet" and not not_met_evidence_ids
                    else f"Documentation needed to establish: {crit.description}"
                )
            ]
            evaluation = CriterionEvaluation(
                id=crit.id,
                description=crit.description,
                met=(status == "met"),
                note=note,
                status=_criterion_status(status),
                supporting_evidence_ids=supporting_evidence_ids,
                not_met_evidence_ids=not_met_evidence_ids,
                missing_evidence=rule_missing,
                reasoning=note,
                confidence_score=_criterion_confidence(status, criterion_evidence_ids),
                review_backend="local",
            )
            if evaluation.note and "context=negated" in evaluation.note.lower():
                evaluation.met = False
                status = "unmet"
                evaluation.status = CriterionStatus.NOT_MET
                evaluation.supporting_evidence_ids = []
                evaluation.not_met_evidence_ids = criterion_evidence_ids
                evaluation.missing_evidence = [f"Evidence for: {crit.description}"]
            detail.append(evaluation)

            if evaluation.met:
                matched_criteria.append(crit.description)
            elif status == "unmet":
                missing_criteria.append(crit.description)
                missing_evidence.append(
                    f"Evidence for: {crit.description}"
                )
            else:  # unknown
                unknown_criteria.append(crit.description)
                missing_criteria.append(crit.description)
                missing_evidence.append(
                    f"Documentation needed to establish: {crit.description}"
                )

        # Contraindications.
        contraindications_found: list[str] = []
        for contra in guideline.contraindications:
            hit = _contains_any(support, contra.keywords) or _contains_any(
                deficiency, contra.keywords
            )
            if hit:
                contraindications_found.append(contra.description)
        for clinical_contra in _medspacy_contraindications(
            support_signals, deficiency_signals
        ):
            if clinical_contra not in contraindications_found:
                contraindications_found.append(clinical_contra)
        for clinical_contra in _clinical_contraindications(case):
            if clinical_contra not in contraindications_found:
                contraindications_found.append(clinical_contra)

        recommendation, confidence = self._decide(
            n_met=len(matched_criteria),
            n_unmet=len(missing_criteria) - len(unknown_criteria),
            n_unknown=len(unknown_criteria),
            n_required=guideline.required_count(),
            has_contraindication=bool(contraindications_found),
        )

        rationale = self._build_rationale(
            case,
            guideline,
            recommendation,
            matched_criteria,
            missing_criteria,
            unknown_criteria,
            contraindications_found,
        )

        recommended_actions = self._recommended_actions(
            recommendation, missing_criteria, contraindications_found
        )

        safety_gate: dict = {}
        unresolved_conflicts = _clinical_conflict_reasons(case)
        if unresolved_conflicts:
            safety_gate["unresolved_conflicts"] = unresolved_conflicts
        human_review_notes = [
            evaluation.note
            for evaluation in detail
            if evaluation.note and "requires human review" in evaluation.note.lower()
        ]
        if human_review_notes:
            safety_gate["requires_human_review_reason"] = human_review_notes[0]
            safety_gate["status"] = "HUMAN_REVIEW_REQUIRED"
            safety_gate["reasons"] = list(dict.fromkeys(human_review_notes))
        medical_exception_reasons = [
            reason
            for reason in (
                case.denial_reason,
                support,
                deficiency,
            )
            if reason
            and any(
                cue in reason.lower()
                for cue in (
                    "medical exception",
                    "exception documentation",
                    "requires human specialist review",
                    "requires human review",
                )
            )
        ]
        if medical_exception_reasons:
            reason = (
                "Medical exception or specialist review is explicitly requested."
            )
            safety_gate["requires_human_review_reason"] = (
                reason
            )
            safety_gate["status"] = "HUMAN_REVIEW_REQUIRED"
            safety_gate["reasons"] = [reason]

        if formulary_rule is not None:
            safety_gate["policy_rules"] = {
                "payer_id": formulary_rule.payer_id,
                "guideline_pack": formulary_rule.guideline_pack,
                "drug_key": formulary_rule.drug_key,
                "prior_authorization_required": formulary_rule.prior_authorization_required,
                "step_therapy_required": formulary_rule.step_therapy_required,
                "quantity_limit": formulary_rule.quantity_limit,
                "step_therapy_new_starts_only": formulary_rule.step_therapy_new_starts_only,
                "source_resource_id": formulary_rule.source_resource_id,
            }

        confidence = self._adjust_confidence(
            base_confidence=confidence,
            detail=detail,
            safety_gate=safety_gate,
            recommendation=recommendation,
        )

        return ReviewResult(
            recommendation=recommendation,
            matched_criteria=matched_criteria,
            missing_criteria=missing_criteria,
            rationale=rationale,
            confidence_score=confidence,
            guideline_id=guideline.guideline_id,
            service_name=guideline.service_name,
            missing_evidence=missing_evidence,
            recommended_actions=recommended_actions,
            contraindications_found=contraindications_found,
            criteria_detail=detail,
            generated_by_ai=False,
            review_backend="local",
            safety_gate=safety_gate,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _evaluate_criterion(
        crit: GuidelineCriterion,
        support: str,
        deficiency: str,
        *,
        raw_support: str | None = None,
        support_signals: list[ClinicalSignal] | None = None,
        deficiency_signals: list[ClinicalSignal] | None = None,
    ) -> tuple[str, str | None]:
        """Return (status, note) where status in {met, unmet, unknown}."""
        marker = f"{crit.id} {crit.description}".lower()
        if ("diagnosis" in marker or "rheumatoid arthritis" in marker) and _has_differential_or_pending_ra(
            raw_support or support
        ):
            return "unknown", "Diagnosis is differential or pending and requires human review."
        if ("diagnosis" in marker or "rheumatoid arthritis" in marker) and _has_missing_psoriasis_severity_metrics(
            raw_support or support
        ):
            return (
                "unmet",
                "Plaque psoriasis severity metrics (BSA or PASI score) are not established.",
            )
        if ("tb_screen" in marker or "tuberculosis" in marker) and _has_missing_tb_documentation(
            raw_support or support
        ):
            return "unmet", "TB screening documentation is stated as missing or not received."
        if ("diagnosis" in marker or "rheumatoid arthritis" in marker) and _has_noncovered_osteoarthritis_indication(
            raw_support or support
        ):
            return "unmet", "Humira is stated as not indicated for the submitted Osteoarthritis diagnosis."
        if (
            "dmard" in marker
            or "methotrexate" in marker
            or "step_therapy" in marker
        ) and _has_step_therapy_refusal(raw_support or support):
            return (
                "unmet",
                "Step therapy refusal/non-adherence documented; methotrexate was not initiated or completed.",
            )
        if (
            "dmard" in marker
            or "methotrexate" in marker
            or "step_therapy" in marker
        ) and _is_non_dmard_step_text(raw_support or support) and not _allows_systemic_step_therapy(crit):
            return (
                "unmet",
                "Only non-DMARD therapy failure is documented; conventional DMARD step therapy is not established.",
            )

        signal_status = _evaluate_with_medspacy(
            crit, support_signals or [], deficiency_signals or []
        )
        if signal_status is not None:
            return signal_status

        keywords = _expanded_keywords(crit)
        flagged = _contains_any(deficiency, keywords)
        supported = _contains_any(support, keywords, ignore_negated=True)

        if flagged:
            return "unmet", f"Denial references this criterion ('{flagged}')."
        if supported:
            return "met", f"Supporting evidence found ('{supported}')."
        return "unknown", "No evidence found in the case or documentation."

    @staticmethod
    def _decide(
        n_met: int,
        n_unmet: int,
        n_unknown: int,
        n_required: int,
        has_contraindication: bool,
    ) -> tuple[Recommendation, float]:
        """Determine recommendation and confidence."""
        if has_contraindication:
            return Recommendation.DENY, 0.9

        if n_required == 0:
            return Recommendation.INSUFFICIENT_INFORMATION, 0.4

        if n_unmet > 0:
            # At least one criterion is explicitly contradicted/deficient.
            confidence = round(min(0.95, 0.7 + 0.1 * n_unmet), 3)
            return Recommendation.DENY, confidence

        if n_met == n_required:
            return Recommendation.APPROVE, 0.9

        # Some criteria are simply unestablished (unknown), none contradicted.
        # Not enough to approve, not enough to deny.
        confidence = round(0.5 + 0.1 * (n_met / max(1, n_required)), 3)
        return Recommendation.INSUFFICIENT_INFORMATION, confidence

    def _adjust_confidence(
        self,
        *,
        base_confidence: float,
        detail: list[CriterionEvaluation],
        safety_gate: dict,
        recommendation: Recommendation,
    ) -> float:
        confidence = base_confidence
        if any(item.status is CriterionStatus.UNKNOWN for item in detail):
            confidence -= 0.08
        if any(not item.supporting_evidence_ids and item.status is CriterionStatus.MET for item in detail):
            confidence -= 0.1
        if any(not item.not_met_evidence_ids and item.status is CriterionStatus.NOT_MET for item in detail):
            confidence -= 0.05
        if safety_gate.get("unresolved_conflicts"):
            confidence -= 0.12
        if safety_gate.get("status") == "HUMAN_REVIEW_REQUIRED":
            confidence -= 0.15
        if recommendation is Recommendation.INSUFFICIENT_INFORMATION:
            confidence -= 0.05
        return round(max(0.0, min(1.0, confidence)), 3)

    def _evaluate_with_policy(
        self,
        crit: GuidelineCriterion,
        formulary_rule: FormularyPolicyRule | None,
    ) -> tuple[str, str | None] | None:
        if formulary_rule is None:
            return None
        marker = f"{crit.id} {crit.description}".lower()
        if "step_therapy" not in marker and "dmard" not in marker and "methotrexate" not in marker:
            return None
        if formulary_rule.step_therapy_required is False:
            return "met", (
                f"Formulary policy for {formulary_rule.drug_key} does not require step therapy."
            )
        return None

    def _formulary_rule_for_case(
        self,
        case: PatientCase,
        guideline: ClinicalGuideline,
    ) -> FormularyPolicyRule | None:
        if self.formulary_policy is None:
            return None
        keys = [
            case.requested_service or "",
            guideline.service_name or "",
            *getattr(guideline, "aliases", []),
        ]
        return self.formulary_policy.rule_for_any(keys, payer_id=self.payer_id)

    @staticmethod
    def _build_rationale(
        case: PatientCase,
        guideline: ClinicalGuideline,
        recommendation: Recommendation,
        matched: list[str],
        missing: list[str],
        unknown: list[str],
        contraindications: list[str],
    ) -> str:
        svc = guideline.service_name
        lines = [
            f"Reviewed request for {svc} against {guideline.guideline_id} "
            f"({guideline.source}, v{guideline.version})."
        ]
        if contraindications:
            lines.append(
                "Contraindication(s) present: " + "; ".join(contraindications) + "."
            )
        if matched:
            lines.append(f"{len(matched)} criterion/criteria met.")
        if missing:
            lines.append(
                f"{len(missing)} criterion/criteria not satisfied: "
                + "; ".join(missing)
                + "."
            )

        if recommendation is Recommendation.APPROVE:
            lines.append(
                "All required medical-necessity criteria are satisfied; the "
                "request meets guideline criteria for approval."
            )
        elif recommendation is Recommendation.DENY:
            lines.append(
                "One or more required criteria are unmet (or a contraindication "
                "exists); a denial is justified under the guideline until the "
                "missing requirements are documented."
            )
        else:
            lines.append(
                "There is insufficient documentation to confirm one or more "
                "criteria. Additional evidence is required before a "
                "determination can be made."
            )
        return " ".join(lines)

    @staticmethod
    def _recommended_actions(
        recommendation: Recommendation,
        missing: list[str],
        contraindications: list[str],
    ) -> list[str]:
        actions: list[str] = []
        if recommendation is Recommendation.APPROVE:
            actions.append("Proceed with authorization; document approval in the record.")
        elif recommendation is Recommendation.DENY:
            if contraindications:
                actions.append(
                    "Address/resolve the contraindication before resubmission."
                )
            for m in missing:
                actions.append(f"Submit documentation for: {m}")
            actions.append(
                "If criteria are met but undocumented, file an appeal with the "
                "supporting clinical records."
            )
        else:
            for m in missing:
                actions.append(f"Obtain and submit documentation for: {m}")
            actions.append("Resubmit the prior authorization once evidence is complete.")
        return actions

    @staticmethod
    def _no_guideline_result(case: PatientCase) -> ReviewResult:
        svc = case.requested_service or "the requested service"
        return ReviewResult(
            recommendation=Recommendation.INSUFFICIENT_INFORMATION,
            matched_criteria=[],
            missing_criteria=[],
            rationale=(
                f"No clinical guideline in the library matched {svc!r}. "
                "A determination cannot be made automatically; manual review "
                "is required."
            ),
            confidence_score=0.3,
            guideline_id=None,
            service_name=case.requested_service,
            missing_evidence=["A matching clinical guideline for the requested service."],
            recommended_actions=[
                "Route to a human reviewer.",
                "Consider adding a guideline for this service to the library.",
            ],
            generated_by_ai=False,
            review_backend="local",
        )
