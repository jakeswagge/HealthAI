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
SEP = r"\s*(?::+|=|\.{2,})\s*"
LOOSE_SEP = r"\s*(?::+|=|#{1,2}|\.{2,})?\s*"
FIELD_LABELS = {
    "patient_name": (
        r"member\s+name",
        r"patient\s+name",
        r"patnt\s+name",
        r"patient",
        r"member",
        r"name",
        r"pt",
    ),
    "member_id": (
        r"member\s*id",
        r"member\s*#",
        r"subscriber\s*id",
        r"memb\s*#",
        r"memb",
        r"id\s*#",
        r"id",
    ),
    "date_of_birth": (r"date\s+of\s+birth", r"do\s*b", r"dob"),
    "diagnosis": (
        r"diaganosis",
        r"primary\s+diagnosis\s+of",
        r"diagnosis\s+of",
        r"diagnosis",
        r"dx",
    ),
    "requested_service": (
        r"req\s+medication",
        r"requested\s+medication",
        r"requested\s+drug",
        r"requested\s+treatment",
        r"requested\s+service",
        r"requested",
        r"service\s+requested",
        r"requesting\s+authorization\s+for",
        r"procedure",
        r"service",
        r"medication",
        r"drug",
        r"req",
    ),
    "insurance_company": (
        r"payer",
        r"insurance\s+company",
        r"health\s+plan",
        r"insurer",
    ),
    "physician_name": (
        r"requesting\s+provider",
        r"ordering\s+provider",
        r"requesting\s+physician",
        r"physician",
        r"provider",
    ),
}
ALL_LABELS = tuple(
    label for labels in FIELD_LABELS.values() for label in labels
) + (
    r"clinical\s+notes?",
    r"clinical\s+summary",
    r"hx",
    r"history",
    r"notes?",
    r"provider",
    r"specialist",
    r"signed",
    r"methotrexate\s+status",
    r"tb\s+status",
    r"tb\s+screen",
    r"primary\s+diagnosis",
    r"due\s+to",
    r"presents",
    r"request\s+status",
    r"status",
    r"decision",
    r"determination",
    r"reason(?:\s+for\s+denial)?",
    r"rationale",
)
NEXT_LABEL_RE = re.compile(rf"\s+(?=(?:{'|'.join(ALL_LABELS)}){SEP})", re.IGNORECASE)
ANY_LABEL_RE = re.compile(rf"^\s*(?:{'|'.join(ALL_LABELS)}){SEP}", re.IGNORECASE)
LOOSE_NEXT_LABEL_RE = re.compile(
    rf"\s+(?=(?:{'|'.join(ALL_LABELS)})\b{LOOSE_SEP})",
    re.IGNORECASE,
)
NOISE_RE = re.compile(r"[*~><]{2,}|-{2,}")


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


def _prepare_text(text: str) -> str:
    """Normalize OCR/fax noise while preserving readable text and newlines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"/{2,}", "\n", text)
    text = text.replace("[", " ").replace("]", " ")
    text = re.sub(r":{2,}", ":", text)
    text = re.sub(r"\s=\s", ":", text)
    text = re.sub(r"(?<=\w)=(?=\w)", ":", text)
    text = NOISE_RE.sub(" ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _strip_value_punctuation(value: str) -> str:
    return value.strip().strip(" .,:;[]{}()*#")


def _clean_value(value: str | None) -> str | None:
    if not value:
        return None
    value = NEXT_LABEL_RE.split(value.splitlines()[0].strip(), maxsplit=1)[0]
    value = LOOSE_NEXT_LABEL_RE.split(value, maxsplit=1)[0]
    value = re.sub(r"\s{2,}", " ", value)
    value = _strip_value_punctuation(value)
    return value or None


def _first_token(value: str | None) -> str | None:
    value = _clean_value(value)
    if not value:
        return None
    m = re.match(r"#?([A-Za-z0-9][A-Za-z0-9_-]*)", value)
    return m.group(1) if m else value


def _normalize_dob(value: str | None) -> str | None:
    value = _clean_value(value)
    if not value:
        return None
    m = re.search(
        r"\b(\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}[- ][A-Za-z]{3,9}[- ]\d{2,4})\b",
        value,
    )
    return m.group(1) if m else value


def _normalize_diagnosis(value: str | None) -> str | None:
    value = _clean_value(value)
    if not value:
        return None
    value = re.sub(r"^(?:of|for)\s+", "", value, flags=re.IGNORECASE)
    low = value.lower()
    if re.fullmatch(r"ra", low):
        return "Rheumatoid Arthritis"
    if "rheumatiod artharitis" in low:
        return "Rheumatoid Arthritis"
    m = re.search(r"\brheumatoid\s+arthritis\b", value, re.IGNORECASE)
    if m and len(value.split()) > len(m.group(0).split()) + 2:
        return m.group(0)
    for diagnosis in (
        "psoriatic arthritis",
        "osteoarthritis",
        "crohn's disease",
        "crohn disease",
    ):
        m = re.search(rf"\b{re.escape(diagnosis)}\b", value, re.IGNORECASE)
        if m and len(value.split()) > len(m.group(0).split()) + 2:
            return m.group(0)
    return value


def _normalize_requested_service(value: str | None) -> str | None:
    value = _clean_value(value)
    if not value:
        return None
    value = re.split(
        r"\b(?:"
        r"is\s+fda-approved|fda-approved|covered\s+under\s+your\s+plan|"
        r"covered\s+for|conditions\s+such\s+as|conditions\s+like|"
        r"approved\s+for|indicated\s+for|educational\s+only"
        r")\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    value = _clean_value(value)
    if not value:
        return None
    m = re.search(r"\b(humeria|humira)(?:\s+\d+\s*mg)?\b", value, re.IGNORECASE)
    if m:
        matched = m.group(0)
        if matched.lower().startswith("humeria"):
            return re.sub(r"(?i)^humeria", "Humira", matched)
        return _strip_value_punctuation(matched)

    known_service = re.search(
        r"\b("
        r"adalimumab|enbrel|etanercept|"
        r"(?:cardiac\s+)?mri(?:\s+[a-z]+){0,4}|"
        r"(?:chest\s+)?ct(?:\s+[a-z]+){0,4}|"
        r"physical\s+therapy|physiotherapy"
        r")\b",
        value,
        re.IGNORECASE,
    )
    if known_service:
        return _strip_value_punctuation(known_service.group(0))

    low = value.lower()
    if any(
        cue in low
        for cue in (
            "fda-approved",
            "covered under your plan",
            "conditions such as",
            "conditions like",
            "approved for",
            "educational only",
        )
    ):
        return None
    if len(value.split()) > 8:
        return None
    return value


def _is_placeholder_or_prose(value: str | None) -> bool:
    if not value:
        return False
    low = value.strip().lower()
    if low in {
        "documentation was not available",
        "not available",
        "n/a",
        "na",
        "none",
        "unknown",
    }:
        return True
    return any(
        phrase in low
        for phrase in (
            "based on the review",
            "appears to meet",
            "medical-necessity criteria",
            "medical necessity criteria",
            "additional clinical evidence",
            "documentation was not available",
        )
    )


def _field_value(field: str, text: str) -> str | None:
    labels = FIELD_LABELS[field]
    pattern = re.compile(rf"\b(?:{'|'.join(labels)}){SEP}(.*)$", re.IGNORECASE)
    label_only = re.compile(rf"^\s*(?:{'|'.join(labels)})\s*$", re.IGNORECASE)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = pattern.search(line)
        if match:
            raw = match.group(1).strip()
            if not raw:
                for follow in lines[index + 1:]:
                    candidate = follow.strip()
                    if not candidate:
                        continue
                    if ANY_LABEL_RE.search(candidate):
                        break
                    raw = candidate
                    break
            value = _clean_value(raw)
            if value:
                return value

        if label_only.search(line):
            for follow in lines[index + 1:]:
                candidate = follow.strip()
                if not candidate:
                    continue
                if ANY_LABEL_RE.search(candidate):
                    break
                value = _clean_value(candidate)
                if value:
                    return value
                break

    if field in {"physician_name", "insurance_company"}:
        return None

    loose_labels = labels
    if field == "requested_service":
        loose_labels = tuple(label for label in labels if label != r"service")
    if field == "diagnosis":
        loose_labels = tuple(label for label in labels if label != r"diagnosis")

    flat = re.sub(r"\s+", " ", text)
    loose = re.compile(
        rf"(?<!\w)(?:{'|'.join(loose_labels)})\b{LOOSE_SEP}(.+?)"
        rf"(?=\s+(?:{'|'.join(ALL_LABELS)})\b{LOOSE_SEP}|\s*$)",
        re.IGNORECASE,
    )
    match = loose.search(flat)
    if match:
        return _clean_value(match.group(1))
    return None


def _clean_name(value: str | None) -> str | None:
    if not value:
        return None
    return _clean_value(value)


def _detect_decision(text: str) -> str:
    # Look for explicit status lines first.
    for match in re.finditer(
        rf"\b(?:request\s+status|status|decision|determination){SEP}([^\n]+)",
        text,
        re.IGNORECASE,
    ):
        status = _clean_value(match.group(1))
        if not status:
            continue
        s = status.lower()
        if any(k in s for k in ("pending", "in review", "under review")):
            return "pending"
        if "partial" in s:
            return "partial"
        if any(
            k in s
            for k in (
                "cannot approve",
                "can't approve",
                "unable to approve",
                "not approved",
                "deni",
                "not medically necessary",
            )
        ):
            return "denied"
        if "deni" in s:
            return "denied"
        if "approv" in s or "authoriz" in s:
            return "approved"

    for line in text.splitlines():
        low = line.lower()
        if re.search(r"\b(if|when|unless)\b.*\b(denied|denial)\b", low):
            continue
        if any(
            k in low
            for k in (
                "adverse determination",
                "coverage is denied",
                "request is denied",
                "has been denied",
                "cannot approve",
                "can't approve",
                "unable to approve",
                "not approved",
                "not medically necessary",
            )
        ):
            return "denied"
        if any(
            k in low
            for k in (
                "favorable determination",
                "coverage is approved",
                "request is approved",
                "has been approved",
                "authorized for",
            )
        ):
            return "approved"
    return "pending"


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
    text = _prepare_text(document_text)

    patient_name = _clean_name(_field_value("patient_name", text))
    member_id = _first_token(_field_value("member_id", text))
    dob = _normalize_dob(_field_value("date_of_birth", text))

    diagnosis = _field_value("diagnosis", text)
    # Strip a leading ICD-10 code embedded in the diagnosis line.
    if diagnosis:
        diagnosis = re.sub(r"^[A-TV-Z][0-9][0-9AB](?:\.[0-9A-Z]{1,4})?\s*\(?", "", diagnosis)
        diagnosis = diagnosis.strip(" ()")
        diagnosis = _normalize_diagnosis(re.sub(r"\s+", " ", diagnosis))

    requested_service = _normalize_requested_service(_field_value("requested_service", text))
    if _is_placeholder_or_prose(requested_service):
        requested_service = None

    insurance_company = _field_value("insurance_company", text)
    if _is_placeholder_or_prose(insurance_company):
        insurance_company = None

    physician_name = _field_value("physician_name", text)
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
            decision not in {"unknown", "pending"},
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
