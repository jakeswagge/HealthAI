"""Post-extraction normalization for structured patient cases.

The extraction prompt asks AI backends to preserve source text. That is good
for auditability, but downstream guideline matching needs canonical clinical
terms. This module applies a small deterministic normalization pass after
schema validation, limited to known medication and diagnosis variants.
"""

from __future__ import annotations

import re

from app.models.patient_case import NormalizedField, PatientCase


_HUMIRA_RE = re.compile(r"\b(?:humeria|humera|humira|adalimumab)\b", re.IGNORECASE)
_ENBREL_RE = re.compile(r"\b(?:enbrel|etanercept)\b", re.IGNORECASE)

_DIAGNOSIS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:rheumatiod|rheumatoid|rhumatoid)\s+arth[a-z]*itis\b",
            re.IGNORECASE,
        ),
        "Rheumatoid Arthritis",
    ),
    (
        re.compile(r"\bpsoriatic\s+arth[a-z]*itis\b", re.IGNORECASE),
        "Psoriatic Arthritis",
    ),
    (
        re.compile(r"\bosteo\s*arth[a-z]*itis\b", re.IGNORECASE),
        "Osteoarthritis",
    ),
    (
        re.compile(r"\bcrohn'?s?\s+disease\b", re.IGNORECASE),
        "Crohn's Disease",
    ),
)

_TEXT_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b((?:quant(?:iferon)?[-\s]*)?tb(?:\s+gold)?|tuberculosis)\s+neg\b",
            re.IGNORECASE,
        ),
        r"\1 negative",
    ),
    (re.compile(r"\bmethatrexat(?:e)?\b", re.IGNORECASE), "methotrexate"),
    (re.compile(r"\bmethotrexat\b", re.IGNORECASE), "methotrexate"),
    (re.compile(r"\brheumatolgy\b", re.IGNORECASE), "rheumatology"),
    (re.compile(r"\brheumatologst\b", re.IGNORECASE), "rheumatologist"),
    (re.compile(r"\bapprvs\b", re.IGNORECASE), "approves"),
    (re.compile(r"\bfaild\b", re.IGNORECASE), "failed"),
    (re.compile(r"\baftr\b", re.IGNORECASE), "after"),
)


def _normalize_service(value: str | None) -> str | None:
    if not value:
        return value
    if _HUMIRA_RE.search(value):
        return "Humira"
    if _ENBREL_RE.search(value):
        return "Enbrel"
    return value


def _normalize_diagnosis(value: str | None) -> str | None:
    if not value:
        return value
    for pattern, canonical in _DIAGNOSIS_PATTERNS:
        if pattern.search(value):
            return canonical
    return value


def _normalize_text(value: str | None) -> str | None:
    if not value:
        return value
    normalized = value
    for pattern, replacement in _TEXT_REPLACEMENTS:
        normalized = pattern.sub(replacement, normalized)
    return normalized


def normalize_clinical_text(text: str | None) -> str:
    """Normalize common clinical misspellings in free-text evidence."""
    return _normalize_text(text) or ""


def normalize_patient_case(case: PatientCase) -> PatientCase:
    """Return a copy of ``case`` with canonical clinical spellings applied."""
    updates: dict[str, str | None] = {}
    raw_fields = dict(case.raw_fields or {})
    normalized_fields = dict(case.normalized_fields or {})

    for field in (
        "diagnosis",
        "requested_service",
        "denial_reason",
        "physician_name",
    ):
        value = getattr(case, field)
        if value is not None and field not in raw_fields:
            raw_fields[field] = value

    service = _normalize_service(case.requested_service)
    if service != case.requested_service:
        updates["requested_service"] = service
        normalized_fields["requested_service"] = NormalizedField(
            raw_value=case.requested_service,
            normalized_value=service,
        )

    diagnosis = _normalize_diagnosis(case.diagnosis)
    if diagnosis != case.diagnosis:
        updates["diagnosis"] = diagnosis
        normalized_fields["diagnosis"] = NormalizedField(
            raw_value=case.diagnosis,
            normalized_value=diagnosis,
        )

    denial_reason = _normalize_text(case.denial_reason)
    if denial_reason != case.denial_reason:
        updates["denial_reason"] = denial_reason
        normalized_fields["denial_reason"] = NormalizedField(
            raw_value=case.denial_reason,
            normalized_value=denial_reason,
        )

    physician_name = _normalize_text(case.physician_name)
    if physician_name != case.physician_name:
        updates["physician_name"] = physician_name
        normalized_fields["physician_name"] = NormalizedField(
            raw_value=case.physician_name,
            normalized_value=physician_name,
        )

    if raw_fields != case.raw_fields:
        updates["raw_fields"] = raw_fields
    if normalized_fields != case.normalized_fields:
        updates["normalized_fields"] = normalized_fields

    if not updates:
        return case
    return case.model_copy(update=updates)
