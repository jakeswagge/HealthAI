"""Deterministic, offline LLM backend (no network, no API key).

This backend does NOT call a real model. Instead it parses the document with
regular expressions and emits the same JSON contract the Claude backend would.

Why it exists:
- The app stays fully runnable locally with no credentials.
- The pytest + evaluation suites are deterministic and fast.
- It is a drop-in stand-in for the real Claude backend (same interface, same
  JSON contract), so swapping in Claude requires zero changes elsewhere.

It is intentionally conservative: when a pattern is not confidently matched it
emits null / [] rather than guessing, mirroring the "never hallucinate" rule.
"""

from __future__ import annotations

import json
import re

from app.services.llm_client import LLMClient, LLMResponse

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------
_DOC_RE = re.compile(
    r"--- BEGIN DOCUMENT ---\s*(.*?)\s*--- END DOCUMENT ---",
    re.DOTALL,
)

ICD10_RE = re.compile(r"\b([A-TV-Z][0-9][0-9AB](?:\.[0-9A-Z]{1,4})?)\b")
CPT_RE = re.compile(r"\b(\d{5})\b")

# Field separator: a colon and/or a run of dots (dotted "leader" layouts),
# surrounded by optional whitespace. Lets one parser handle both
# "Label: value" and "Label ......... value" styles.
SEP = r"\s*[:.]+\s*"


def _extract_document_text(messages: list[dict[str, str]]) -> str:
    """Pull the raw document text back out of the user prompt."""
    content = ""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
    match = _DOC_RE.search(content)
    if match:
        return match.group(1)
    return content


def _find(pattern: str, text: str, group: int = 1) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    value = m.group(group).strip()
    # Trim trailing label noise / empty.
    return value or None


def _clean_name(value: str | None) -> str | None:
    if not value:
        return None
    # Stop at line breaks and collapse internal whitespace.
    value = value.splitlines()[0].strip()
    value = re.sub(r"\s{2,}", " ", value)
    return value or None


def _detect_decision(text: str) -> str:
    lowered = text.lower()
    # Look for explicit status lines first.
    status = _find(r"status\s*:\s*([a-z ]+)", text)
    if status:
        s = status.lower()
        if "partial" in s:
            return "partial"
        if "deni" in s:
            return "denied"
        if "approv" in s:
            return "approved"
    if "partially approved" in lowered or "partial approval" in lowered:
        return "partial"
    if any(k in lowered for k in ("adverse determination", "denied", "denial", "not medically necessary")):
        return "denied"
    if any(k in lowered for k in ("favorable determination", "approved", "authorized", "certif")):
        return "approved"
    return "unknown"


def _extract_denial_reason(text: str, decision: str) -> str | None:
    if decision != "denied":
        return None
    # Prefer a multi-line "Rationale:" / "Reason:" block that ends at a blank
    # line, a separator rule, or a known following section.
    m = re.search(
        r"(?:rationale|reason(?:\s+for\s+denial)?)\s*:\s*(.+?)"
        r"(?:\n\s*\n|\n[-=]{3,}|clinical criteria|appeal|\Z)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        reason = re.sub(r"\s+", " ", m.group(1)).strip()
        if reason:
            return reason

    # Fallback: capture just the remainder of the rationale/reason line.
    m = re.search(
        r"(?:rationale|reason(?:\s+for\s+denial)?)\s*:\s*([^\n]+)",
        text,
        re.IGNORECASE,
    )
    if m:
        reason = m.group(1).strip()
        return reason or None
    return None


def _parse(document_text: str) -> dict:
    """Parse known prior-authorization fields from raw document text."""
    text = document_text

    patient_name = _clean_name(
        _find(rf"(?:member\s+name|patient\s+name|patient|member){SEP}(.+)", text)
    )
    member_id = _find(
        rf"\b(?:member\s*id|member\s*#|subscriber\s*id|id\s*#){SEP}([A-Z0-9][A-Z0-9\-]+)",
        text,
    )
    dob = _find(
        rf"(?:date\s+of\s+birth|dob){SEP}([0-9]{{1,2}}/[0-9]{{1,2}}/[0-9]{{2,4}})",
        text,
    )

    diagnosis = _clean_name(
        _find(rf"(?:diagnosis|dx){SEP}(.+)", text)
    )
    # Strip a leading ICD-10 code embedded in the diagnosis line.
    if diagnosis:
        diagnosis = re.sub(r"^[A-TV-Z][0-9][0-9AB](?:\.[0-9A-Z]{1,4})?\s*\(?", "", diagnosis)
        diagnosis = diagnosis.strip(" ()")
        diagnosis = re.sub(r"\s+", " ", diagnosis) or None

    requested_service = _clean_name(
        _find(rf"(?:procedure|requested\s+service|service){SEP}(.+)", text)
    )

    insurance_company = _clean_name(
        _find(rf"(?:payer|insurance\s+company|health\s+plan|insurer){SEP}(.+)", text)
    )

    physician_name = _clean_name(
        _find(
            rf"(?:requesting\s+provider|ordering\s+provider|requesting\s+physician|physician|provider){SEP}(.+)",
            text,
        )
    )
    if physician_name:
        # Drop trailing specialty in parens kept by some layouts? Keep as-is;
        # but strip an "NPI" fragment if it bled onto the same line.
        physician_name = re.split(r"\bNPI\b", physician_name, flags=re.IGNORECASE)[0].strip()

    # Codes: search the whole document.
    icd10_codes = []
    # Look within diagnosis context lines preferentially, then whole doc.
    for code in ICD10_RE.findall(text):
        if code not in icd10_codes:
            icd10_codes.append(code)

    cpt_codes = []
    cpt_context = re.search(r"cpt[^\n]*", text, re.IGNORECASE)
    search_space = cpt_context.group(0) if cpt_context else ""
    for code in CPT_RE.findall(search_space):
        if code not in cpt_codes:
            cpt_codes.append(code)

    decision = _detect_decision(text)
    denial_reason = _extract_denial_reason(text, decision)

    # Confidence: proportion of the core fields we managed to fill.
    core_filled = sum(
        bool(x)
        for x in [
            patient_name,
            member_id,
            dob,
            diagnosis,
            icd10_codes,
            requested_service,
            cpt_codes,
            insurance_company,
            decision != "unknown",
            physician_name,
        ]
    )
    confidence = round(0.35 + 0.6 * (core_filled / 10), 4)

    return {
        "patient_name": patient_name,
        "member_id": member_id,
        "date_of_birth": dob,
        "diagnosis": diagnosis,
        "icd10_codes": icd10_codes,
        "requested_service": requested_service,
        "cpt_codes": cpt_codes,
        "insurance_company": insurance_company,
        "decision": decision,
        "denial_reason": denial_reason,
        "physician_name": physician_name,
        "confidence_score": confidence,
    }


class LocalHeuristicClient(LLMClient):
    """Offline, deterministic backend that mimics the JSON extraction contract."""

    name = "local-heuristic"

    @property
    def is_ai(self) -> bool:
        return False

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> LLMResponse:
        document_text = _extract_document_text(messages)
        parsed = _parse(document_text)
        return LLMResponse(
            text=json.dumps(parsed),
            model=self.name,
            raw={"backend": "regex"},
        )
