"""MedSpaCy-backed clinical signal extraction for deterministic review.

This module intentionally exposes a small, app-specific signal contract rather
than leaking spaCy objects into the review engine. If MedSpaCy is unavailable in
an environment, callers get an empty signal list and can fall back to legacy
deterministic matching.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

warnings.filterwarnings(
    "ignore",
    message=r".*component 'matcher' does not have any patterns defined.*",
    category=UserWarning,
)


@dataclass(frozen=True)
class ClinicalSignal:
    """A clinical target mention plus its MedSpaCy ConText attributes."""

    label: str
    text: str
    start_char: int
    end_char: int
    sentence: str
    is_negated: bool = False
    is_historical: bool = False
    is_hypothetical: bool = False
    is_uncertain: bool = False
    is_family: bool = False
    is_differential: bool = False

    @property
    def is_current_affirmed(self) -> bool:
        return not (
            self.is_negated
            or self.is_historical
            or self.is_hypothetical
            or self.is_uncertain
            or self.is_family
            or self.is_differential
        )


_TARGET_RULES: tuple[tuple[str, str], ...] = (
    ("Rheumatoid Arthritis", "DIAGNOSIS_RA"),
    ("RA", "DIAGNOSIS_RA"),
    ("Psoriatic Arthritis", "DIAGNOSIS_PSORIATIC_ARTHRITIS"),
    ("PsA", "DIAGNOSIS_PSORIATIC_ARTHRITIS"),
    ("Methotrexate", "STEP_THERAPY"),
    ("MTX", "STEP_THERAPY"),
    ("DMARD", "STEP_THERAPY"),
    ("TB", "TB"),
    ("Tuberculosis", "TB"),
    ("TB screen", "TB"),
    ("TB screening", "TB"),
    ("TB test", "TB"),
    ("Quantiferon", "TB"),
    ("Quantiferon Gold", "TB"),
    ("Quantiferon-TB", "TB"),
    ("Quantiferon-TB Gold", "TB"),
    ("T-Spot", "TB"),
    ("PPD", "TB"),
    ("Humira", "BIOLOGIC_HUMIRA"),
    ("Adalimumab", "BIOLOGIC_HUMIRA"),
    ("Hepatitis B", "HEP_B"),
    ("Hep B", "HEP_B"),
    ("HBV", "HEP_B"),
    ("Enbrel", "BIOLOGIC_ENBREL"),
    ("Etanercept", "BIOLOGIC_ENBREL"),
    ("Rheum", "SPECIALIST_RHEUM"),
    ("Rheumatologist", "SPECIALIST_RHEUM"),
    ("Rheumatology", "SPECIALIST_RHEUM"),
    ("Derm", "SPECIALIST_DERM"),
    ("Dermatologist", "SPECIALIST_DERM"),
    ("Dermatology", "SPECIALIST_DERM"),
    ("Specialist", "SPECIALIST_RHEUM"),
    ("Chiro", "PROVIDER_CHIROPRACTIC"),
    ("Chiropractor", "PROVIDER_CHIROPRACTIC"),
    ("Chiropractic", "PROVIDER_CHIROPRACTIC"),
)

_DIFFERENTIAL_RE = re.compile(
    r"\b(differential|rule\s*out|r/o|possible|suspected|consider(?:ing)?)\b",
    re.IGNORECASE,
)
_DIAGNOSIS_CANONICAL = {
    "DIAGNOSIS_RA": "Rheumatoid Arthritis",
    "DIAGNOSIS_PSORIATIC_ARTHRITIS": "Psoriatic Arthritis",
}
_SPECIALIST_ROLE_CANONICAL = {
    "SPECIALIST_RHEUM": "rheumatology specialist",
    "SPECIALIST_DERM": "dermatology specialist",
}
_PROVIDER_ROLE_CANONICAL = {
    **_SPECIALIST_ROLE_CANONICAL,
    "PROVIDER_CHIROPRACTIC": "chiropractic provider",
}
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
    "not performed",
    "not documented",
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
_STEP_FAILURE_CUES = (
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
_STEP_REFUSAL_CUES = (
    "refused",
    "refusal",
    "declined",
    "declines",
    "patient refused",
    "patient declined",
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
)


def _bool_ext(ent: Any, name: str) -> bool:
    try:
        return bool(getattr(ent._, name))
    except Exception:
        return False


def _has_any(value: str, cues: tuple[str, ...]) -> bool:
    low = value.lower()
    return any(cue in low for cue in cues)


@lru_cache(maxsize=1)
def get_clinical_nlp():
    """Return a cached MedSpaCy pipeline, or None if initialization fails."""
    try:
        from loguru import logger

        logger.disable("PyRuSH")
    except Exception:
        pass

    try:
        import medspacy
        from medspacy.target_matcher import TargetRule
    except Exception:
        return None

    try:
        nlp = medspacy.load()
        if "medspacy_target_matcher" not in nlp.pipe_names:
            nlp.add_pipe("medspacy_target_matcher")
        if "medspacy_context" not in nlp.pipe_names:
            nlp.add_pipe("medspacy_context", last=True)

        matcher = nlp.get_pipe("medspacy_target_matcher")
        matcher.add([TargetRule(literal, label) for literal, label in _TARGET_RULES])
        return nlp
    except Exception:
        return None


def extract_clinical_signals(text: str) -> list[ClinicalSignal]:
    """Extract configured clinical targets with ConText attributes."""
    if not text or not text.strip():
        return []

    nlp = get_clinical_nlp()
    if nlp is None:
        return []

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r".*component 'matcher' does not have any patterns defined.*",
            category=UserWarning,
        )
        doc = nlp(text)
    signals: list[ClinicalSignal] = []
    seen: set[tuple[str, int, int]] = set()

    for ent in doc.ents:
        key = (ent.label_, ent.start_char, ent.end_char)
        if key in seen:
            continue
        seen.add(key)
        sentence = ent.sent.text if ent.sent is not None else ent.text
        signals.append(
            ClinicalSignal(
                label=ent.label_,
                text=ent.text,
                start_char=ent.start_char,
                end_char=ent.end_char,
                sentence=sentence,
                is_negated=_bool_ext(ent, "is_negated"),
                is_historical=_bool_ext(ent, "is_historical"),
                is_hypothetical=_bool_ext(ent, "is_hypothetical"),
                is_uncertain=_bool_ext(ent, "is_uncertain"),
                is_family=_bool_ext(ent, "is_family"),
                is_differential=bool(_DIFFERENTIAL_RE.search(sentence)),
            )
        )

    return sorted(signals, key=lambda s: (s.start_char, s.end_char, s.label))


def canonical_diagnosis(signal: ClinicalSignal) -> str | None:
    """Return the canonical active diagnosis represented by a signal."""
    if not signal.is_current_affirmed:
        return None
    return _DIAGNOSIS_CANONICAL.get(signal.label)


def specialist_role(signal: ClinicalSignal) -> str | None:
    """Return a normalized accepted specialist role for current evidence."""
    if not signal.is_current_affirmed:
        return None
    return _SPECIALIST_ROLE_CANONICAL.get(signal.label)


def provider_role(signal: ClinicalSignal) -> str | None:
    """Return a normalized provider role, including unsupported roles."""
    if not signal.is_current_affirmed:
        return None
    return _PROVIDER_ROLE_CANONICAL.get(signal.label)


def step_therapy_status(signal: ClinicalSignal) -> str | None:
    """Classify a step-therapy mention as failed, refused, absent, or unknown."""
    if signal.label != "STEP_THERAPY":
        return None
    if (
        signal.is_hypothetical
        or signal.is_uncertain
        or signal.is_family
        or signal.is_differential
    ):
        return None
    if _has_any(signal.sentence, _STEP_REFUSAL_CUES):
        return "refused"
    if signal.is_negated or _has_any(signal.sentence, _STEP_ABSENCE_CUES):
        return "absent"
    if _has_any(signal.sentence, _STEP_FAILURE_CUES):
        return "failed"
    return "unknown"


def tb_result_polarity(signal: ClinicalSignal) -> str | None:
    """Classify a TB target mention as positive, negative, absent, or unknown."""
    if signal.label != "TB":
        return None
    if signal.is_historical or signal.is_hypothetical or signal.is_uncertain:
        return None
    if _has_any(signal.sentence, _TB_ABSENCE_CUES):
        return "absent"
    if signal.is_negated or _has_any(signal.sentence, _TB_NEGATIVE_CUES):
        return "negative"
    if _has_any(signal.sentence, _TB_POSITIVE_CUES):
        return "positive"
    if _has_any(signal.text, _TB_TEST_CUES) or _has_any(signal.sentence, _TB_TEST_CUES):
        return "unknown"
    return "positive"
