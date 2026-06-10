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
from app.models.patient_case import PatientCase
from app.models.review_result import (
    CriterionEvaluation,
    CriterionStatus,
    Recommendation,
    ReviewResult,
)
from app.review.clinical_nlp import (
    ClinicalSignal,
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
    before = text[max(0, start - 80):start]
    after = text[end : min(len(text), end + 80)]
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
_SPECIALIST_ABSENCE_CUES = (
    "no specialist",
    "no rheumatologist",
    "no rheumatology",
    "not documented",
    "missing",
    "without specialist",
    "without rheumatology",
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
            parts.extend(("failed methotrexate", "methotrexate failure"))
        elif step.lower() == "refused":
            parts.append("methotrexate refused")
        elif step.lower() == "absent":
            parts.append("no methotrexate trial")

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
        facts.extend(("specialist_status", "provider_role"))
    if "tb_screen" in marker or "tuberculosis" in marker:
        facts.append("tb_screen_result")
    if "dmard" in marker or "methotrexate" in marker or "step_therapy" in marker:
        facts.append("step_therapy_status")
    return _source_ids_for(case, tuple(facts))


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
    # A bare disease mention without testing language is treated as active risk.
    if _low(signal.text) in {"tb", "tuberculosis"} and not _has_any(signal.sentence, _TB_TEST_CUES):
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

        if _has_missing_tb_documentation(" ".join(s.sentence for s in support_signals)):
            return "unmet", "TB screening documentation is stated as missing or not received."

        tb_support = _signals(support_signals, "TB")
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
        if refused:
            return "unmet", _signal_note("Step therapy refusal documented", refused)
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
        specialist_labels = ("SPECIALIST_RHEUM", "SPECIALIST_DERM")
        specialist_def = _signals_any(deficiency_signals, specialist_labels)
        if specialist_def:
            return "unmet", _signal_note("Denial references specialist involvement", specialist_def[0])

        specialist_support = _signals_any(support_signals, specialist_labels)
        absent = next(
            (
                s for s in specialist_support
                if s.is_negated or _has_any(s.sentence, _SPECIALIST_ABSENCE_CUES)
            ),
            None,
        )
        met = next((s for s in specialist_support if s.is_current_affirmed), None)
        if met:
            return "met", _signal_note("Specialist evidence found", met)
        if absent:
            return "unmet", _signal_note("Specialist absence documented", absent)

    if "diagnosis" in marker or "rheumatoid arthritis" in marker:
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

    def __init__(self, repository: GuidelineRepository | None = None):
        self.repository = repository or get_default_repository()

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

        matched_criteria: list[str] = []
        missing_criteria: list[str] = []
        missing_evidence: list[str] = []
        unknown_criteria: list[str] = []
        detail: list[CriterionEvaluation] = []

        for crit in guideline.required_criteria:
            status, note = self._evaluate_criterion(
                crit,
                support,
                deficiency,
                raw_support=raw_support,
                support_signals=support_signals,
                deficiency_signals=deficiency_signals,
            )
            criterion_evidence_ids = (
                _evidence_ids_for_criterion(case, crit) if status == "met" else []
            )
            rule_missing = [] if status == "met" else [
                (
                    f"Evidence for: {crit.description}"
                    if status == "unmet"
                    else f"Documentation needed to establish: {crit.description}"
                )
            ]
            evaluation = CriterionEvaluation(
                id=crit.id,
                description=crit.description,
                met=(status == "met"),
                note=note,
                status=_criterion_status(status),
                supporting_evidence_ids=criterion_evidence_ids,
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
        if ("tb_screen" in marker or "tuberculosis" in marker) and _has_missing_tb_documentation(
            raw_support or support
        ):
            return "unmet", "TB screening documentation is stated as missing or not received."
        if ("diagnosis" in marker or "rheumatoid arthritis" in marker) and _has_noncovered_osteoarthritis_indication(
            raw_support or support
        ):
            return "unmet", "Humira is stated as not indicated for the submitted Osteoarthritis diagnosis."

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
