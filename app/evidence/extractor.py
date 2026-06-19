"""Deterministic evidence extraction from a CaseDocument.

For each logical fact we care about (patient_name, member_id, diagnosis,
requested_service, denial_reason, icd10_codes, cpt_codes, ...) this scans the
document page-by-page, and when a value is found records an
:class:`EvidenceReference` with:

- the exact 1-indexed page,
- the line/label the value sits under (section_label),
- the verbatim quoted line (quoted_text),
- a normalized "fact_type: value" string,
- a confidence score.

This is regex-based and fully offline so traceability never depends on a live
model. It deliberately never invents values: if a pattern is not matched, no
evidence is produced for that fact.
"""

from __future__ import annotations

import re

from app.ingestion.classifier import detect_document_sections
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.evidence_reference import EvidenceReference
from app.review.clinical_nlp import (
    assertion_token,
    canonical_diagnosis,
    extract_clinical_signals,
    mentioned_diagnosis,
    provider_role,
    specialist_role,
    step_therapy_status,
    tb_result_polarity,
)

# Logical fact types the extractor knows how to find.
FACT_TYPES: tuple[str, ...] = (
    "patient_name",
    "member_id",
    "date_of_birth",
    "diagnosis",
    "diagnosis_assertion",
    "requested_service",
    "insurance_company",
    "physician_name",
    "decision",
    "denial_reason",
    "icd10_codes",
    "cpt_codes",
    "tb_screen_result",
    "provider_role",
    "specialist_status",
    "step_therapy_status",
    "prior_auth_status",
    "claim_denial_reason",
)

_SEP = r"\s*[:.]+\s*"

_CONVENTIONAL_DMARD_LABELS = (
    ("methotrexate", ("methotrexate", "mtx")),
    ("azathioprine", ("azathioprine", "aza")),
    ("mercaptopurine", ("mercaptopurine", "6-mp")),
    ("thiopurine", ("thiopurine",)),
)

_SYSTEMIC_STEP_LABELS = (
    ("systemic therapy", ("systemic therapy",)),
    ("phototherapy", ("phototherapy",)),
)


def _step_therapy_failure_phrase(text: str) -> str:
    low = (text or "").lower()
    for label, cues in _CONVENTIONAL_DMARD_LABELS:
        if any(cue in low for cue in cues):
            return f"{label} failure"
    for label, cues in _SYSTEMIC_STEP_LABELS:
        if any(cue in low for cue in cues):
            return "step therapy failure"
    if "dmard" in low:
        return "conventional DMARD failure"
    return "step therapy failure"

_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "patient_name": (r"member\s+name", r"patient\s+name", r"patient", r"member"),
    "member_id": (r"member\s*id", r"member\s*#", r"subscriber\s*id", r"id\s*#", r"id"),
    "date_of_birth": (r"date\s+of\s+birth", r"dob"),
    "diagnosis": (r"diagnosis", r"dx"),
    "requested_service": (
        r"requested\s+medication",
        r"requested\s+drug",
        r"requested\s+treatment",
        r"requested\s+service",
        r"procedure",
        r"service",
        r"medication",
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

_ALL_LABELS = tuple(
    label
    for labels in _FIELD_LABELS.values()
    for label in labels
) + (
    r"request\s+status",
    r"status",
    r"decision",
    r"determination",
    r"reason(?:\s+for\s+denial)?",
    r"rationale",
)
_NEXT_LABEL_RE = re.compile(
    rf"\s+(?=(?:{'|'.join(_ALL_LABELS)}){_SEP})",
    re.IGNORECASE,
)
_ANY_LABEL_RE = re.compile(
    rf"^\s*(?:{'|'.join(_ALL_LABELS)}){_SEP}",
    re.IGNORECASE,
)

ICD10_RE = re.compile(r"\b([A-TV-Z][0-9][0-9AB](?:\.[0-9A-Z]{1,4})?)\b")
CPT_RE = re.compile(r"\b(\d{5})\b")

_CRITERION_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "criterion_specialist",
        (
            "reviewed by rheumatology service",
            "under care of rheumatology",
            "board-certified rheumatologist",
            "rheumatology consultation",
            "specialist consultation",
            "rheumatology clinic",
            "specialist evaluation",
            "evaluated by rheumatology",
            "evaluated by specialist",
            "referred to rheumatology",
            "consulting rheumatologist",
            "seen by specialist",
            "seen by rheumatology",
            "seen by rheumatologist",
            "rheumatologist",
            "rheumatology",
            "rheum",
            "specialist",
            "gastroenterologist",
            "dermatologist",
        ),
    ),
    (
        "criterion_tb_screen",
        (
            "quantiferon-tb gold negative",
            "tuberculosis screening negative",
            "tuberculosis test negative",
            "latent tb screening",
            "negative tb result",
            "quantiferon gold",
            "quantiferon-tb",
            "tb test negative",
            "negative tb screen",
            "tb screen negative",
            "t-spot",
            "quantiferon",
            "tb test",
            "tb screen",
            "tuberculosis",
        ),
    ),
    (
        "criterion_step_therapy",
        (
            "persistent symptoms despite methotrexate",
            "uncontrolled disease on methotrexate",
            "inadequate response to methotrexate",
            "conventional dmard failure",
            "refractory to methotrexate",
            "methotrexate ineffective",
            "failed methotrexate",
            "methotrexate trial",
            "dmard failure",
            "step therapy",
            "conventional therapy",
            "methotrexate",
            "dmard",
            "failed",
        ),
    ),
)

_STEP_REFUSAL_DOCUMENT_RE = re.compile(
    r"\b(?:methotrexate|mtx|dmard)\b.{0,220}\b"
    r"(?:refus(?:ed|es|al)|declin(?:ed|es)|non[-\s]?compliant|"
    r"non[-\s]?adherent|never\s+(?:started|initiated)|"
    r"did\s+not\s+(?:start|initiate|fill|take|ingest)|"
    r"not\s+(?:started|initiated|filled|taken|ingested)|"
    r"would\s+not\s+(?:start|fill|take|ingest)|"
    r"fear(?:ful)?\s+of\s+side\s+effects|afraid\s+of\s+side\s+effects|"
    r"concern(?:ed)?\s+about\s+side\s+effects|direct\s+biologic)",
    re.IGNORECASE | re.DOTALL,
)
_STEP_REFUSAL_LINE_CUES = (
    "refused",
    "refusal",
    "declined",
    "non-compliant",
    "non compliant",
    "noncompliant",
    "non-adherent",
    "non adherent",
    "nonadherent",
    "never started",
    "never initiated",
    "did not start",
    "did not initiate",
    "did not fill",
    "did not take",
    "did not ingest",
    "would not start",
    "would not fill",
    "would not take",
    "would not ingest",
    "fear of side effects",
    "afraid of side effects",
    "concerned about side effects",
    "direct biologic",
)

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


def _phrase_pattern(phrase: str) -> re.Pattern:
    escaped = re.escape(phrase.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\-", r"[-\s]?")
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def _is_negated_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 80):start]
    after = text[end : min(len(text), end + 80)]
    return bool(_NEGATION_BEFORE_RE.search(before) or _NEGATION_AFTER_RE.search(after))


def _matched_positive_phrase(text: str, phrases: tuple[str, ...]) -> str | None:
    for phrase in phrases:
        for match in _phrase_pattern(phrase).finditer(text):
            if _is_negated_context(text, match.start(), match.end()):
                continue
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


def _clean(value: str) -> str:
    value = _NEXT_LABEL_RE.split(value.splitlines()[0].strip(), maxsplit=1)[0]
    return re.sub(r"\s{2,}", " ", value).strip(" .")


def _is_placeholder_or_prose(value: str) -> bool:
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


def _label_pattern(labels: tuple[str, ...]) -> re.Pattern:
    return re.compile(rf"\b(?:{'|'.join(labels)}){_SEP}(.*)$", re.IGNORECASE)


def _labeled_value(
    lines: list[str], line_index: int, labels: tuple[str, ...]
) -> tuple[str, str] | None:
    """Return (value, quote) for a label, allowing value on the next line."""
    match = _label_pattern(labels).search(lines[line_index])
    if not match:
        return None

    quote = lines[line_index].strip()
    raw = match.group(1).strip()
    if not raw:
        for follow in lines[line_index + 1:]:
            candidate = follow.strip()
            if not candidate:
                continue
            if _ANY_LABEL_RE.search(candidate):
                break
            raw = candidate
            quote = f"{quote}\n{candidate}"
            break

    value = _clean(raw)
    return (value, quote) if value else None


def _primary_diagnosis_context(text: str) -> tuple[str | None, bool]:
    """Return (canonical diagnosis, excluded) for recognized diagnosis context."""
    diagnosis_signals = [
        signal
        for signal in extract_clinical_signals(text)
        if signal.label.startswith("DIAGNOSIS_")
    ]
    for signal in diagnosis_signals:
        canonical = canonical_diagnosis(signal)
        if canonical:
            return canonical, False
    return None, bool(diagnosis_signals)


def _clean_sentence(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _section_label(line: str) -> str | None:
    """Return the label portion (before the colon) of a 'label: value' line."""
    m = re.match(r"\s*([A-Za-z0-9 /#()\-]+?)\s*[:.]", line)
    return m.group(1).strip() if m else None


def _step_refusal_quote(page_text: str, lines: list[str]) -> str | None:
    """Return the best quote for split-sentence methotrexate refusal evidence."""
    if not _STEP_REFUSAL_DOCUMENT_RE.search(page_text):
        return None
    for line in lines:
        low = line.lower()
        if any(cue in low for cue in _STEP_REFUSAL_LINE_CUES):
            return line.strip()
    for line in lines:
        if "methotrexate" in line.lower() or "mtx" in line.lower():
            return line.strip()
    return None


class EvidenceExtractor:
    """Produce :class:`EvidenceReference` objects from a document."""

    def extract(self, document: CaseDocument) -> list[EvidenceReference]:
        """Extract all evidence references found in the document."""
        evidence: list[EvidenceReference] = []
        sections = detect_document_sections(document)

        for section in sections:
            for offset, page_text in enumerate(section.pages()):
                page_number = section.page_start + offset
                refs = self._extract_page(document, page_number, page_text)
                for ref in refs:
                    ref.section_label = _merge_section_label(
                        section.section_type.value,
                        ref.section_label,
                    )
                evidence.extend(refs)

        return evidence

    # ------------------------------------------------------------------ #
    # Per-page extraction
    # ------------------------------------------------------------------ #
    def _make_ref(
        self,
        document: CaseDocument,
        page_number: int,
        fact_type: str,
        value: str,
        quoted_line: str,
        confidence: float,
    ) -> EvidenceReference:
        return EvidenceReference(
            case_id=document.case_id,
            source_document_id=document.document_id,
            source_filename=document.filename,
            page_number=page_number,
            section_label=_section_label(quoted_line),
            quoted_text=quoted_line.strip(),
            normalized_fact=f"{fact_type}: {value}",
            fact_type=fact_type,
            confidence_score=confidence,
        )

    def _extract_page(
        self, document: CaseDocument, page_number: int, page_text: str
    ) -> list[EvidenceReference]:
        refs: list[EvidenceReference] = []
        if not page_text.strip():
            return refs

        lines = page_text.splitlines()

        # --- label/value facts ---
        for fact_type, labels in _FIELD_LABELS.items():
            for index, line in enumerate(lines):
                extracted = _labeled_value(lines, index, labels)
                if extracted is None:
                    continue
                value, quoted_line = extracted
                if fact_type == "member_id":
                    m = re.match(r"[A-Za-z0-9][A-Za-z0-9_-]*", value)
                    if m:
                        value = m.group(0)
                if fact_type == "diagnosis":
                    # Strip an embedded leading ICD-10 code from the value.
                    value = re.sub(
                        r"^[A-TV-Z][0-9][0-9AB](?:\.[0-9A-Z]{1,4})?\s*\(?",
                        "",
                        value,
                    ).strip(" ()")
                    canonical, excluded = _primary_diagnosis_context(quoted_line)
                    if excluded:
                        continue
                    if canonical:
                        value = canonical
                if fact_type == "physician_name":
                    value = re.split(r"\bNPI\b", value, flags=re.IGNORECASE)[0].strip()
                if fact_type in {"requested_service", "insurance_company"}:
                    if _is_placeholder_or_prose(value):
                        continue
                if not value:
                    continue
                refs.append(
                    self._make_ref(
                        document, page_number, fact_type, value, quoted_line, 0.9
                    )
                )
                break  # first match per fact per page is enough

        # --- decision + denial reason ---
        decision = self._detect_decision(page_text)
        if decision:
            dec_line = self._find_line(lines, ("status", "decision", "determination")) or decision
            refs.append(
                self._make_ref(document, page_number, "decision", decision, dec_line, 0.85)
            )
            if decision == "denied":
                reason = self._denial_reason(page_text)
                if reason:
                    reason_line = self._find_line(lines, ("reason", "rationale")) or reason[:80]
                    refs.append(
                        self._make_ref(
                            document, page_number, "denial_reason", reason, reason_line, 0.8
                        )
                    )
                    claim_reason = self._claim_denial_reason(reason)
                    if claim_reason:
                        refs.append(
                            self._make_ref(
                                document,
                                page_number,
                                "claim_denial_reason",
                                claim_reason,
                                reason_line,
                                0.8,
                            )
                        )

        prior_auth_status = self._prior_auth_status(page_text)
        if prior_auth_status:
            line = self._find_line(
                lines,
                ("prior authorization", "prior auth", "pa number", "authorization number"),
            ) or prior_auth_status
            refs.append(
                self._make_ref(
                    document,
                    page_number,
                    "prior_auth_status",
                    prior_auth_status,
                    line,
                    0.8,
                )
            )

        # --- code lists ---
        for code in self._unique(ICD10_RE.findall(page_text)):
            line = self._find_line(lines, (code.lower(),)) or code
            refs.append(self._make_ref(document, page_number, "icd10_codes", code, line, 0.8))

        cpt_context = "\n".join(l for l in lines if "cpt" in l.lower() or "hcpcs" in l.lower())
        for code in self._unique(CPT_RE.findall(cpt_context)):
            line = self._find_line(lines, (code,)) or code
            refs.append(self._make_ref(document, page_number, "cpt_codes", code, line, 0.8))

        seen_criteria: set[str] = set()
        for line in lines:
            for fact_type, phrases in _CRITERION_PHRASES:
                if fact_type in seen_criteria:
                    continue
                matched = _matched_positive_phrase(line, phrases)
                if not matched:
                    continue
                refs.append(
                    self._make_ref(
                        document,
                        page_number,
                        fact_type,
                        matched,
                        line,
                        0.75,
                    )
                )
                seen_criteria.add(fact_type)

        clinical_refs = self._extract_clinical_signal_refs(
            document,
            page_number,
            page_text,
        )
        refusal_quote = _step_refusal_quote(page_text, lines)
        if (
            refusal_quote
            and not any(r.fact_type == "step_therapy_status" for r in clinical_refs)
        ):
            clinical_refs.append(
                self._make_ref(
                    document,
                    page_number,
                    "step_therapy_status",
                    "refused",
                    refusal_quote,
                    0.85,
                )
            )
        if clinical_refs:
            clinical_fact_types = {r.fact_type for r in clinical_refs}
            if "criterion_specialist" in clinical_fact_types:
                refs = [
                    r
                    for r in refs
                    if r.fact_type != "criterion_specialist"
                ]
            if "tb_screen_result" in clinical_fact_types:
                refs = [
                    r
                    for r in refs
                    if r.fact_type != "criterion_tb_screen"
                ]
            if "step_therapy_status" in clinical_fact_types:
                refs = [
                    r
                    for r in refs
                    if r.fact_type != "criterion_step_therapy"
                ]
            refs.extend(clinical_refs)

        return refs

    # ------------------------------------------------------------------ #
    # Small helpers
    # ------------------------------------------------------------------ #
    def _extract_clinical_signal_refs(
        self,
        document: CaseDocument,
        page_number: int,
        page_text: str,
    ) -> list[EvidenceReference]:
        refs: list[EvidenceReference] = []
        seen: set[tuple[str, str, str]] = set()

        for signal in extract_clinical_signals(page_text):
            quote = _clean_sentence(signal.sentence)
            if not quote:
                continue

            mentioned = mentioned_diagnosis(signal)
            if mentioned and not signal.is_current_affirmed:
                state = assertion_token(signal)
                key = ("diagnosis_assertion", f"{mentioned}|{state}", quote)
                if key not in seen:
                    seen.add(key)
                    refs.append(
                        self._make_ref(
                            document,
                            page_number,
                            "diagnosis_assertion",
                            f"{mentioned}|{state}",
                            quote,
                            0.75,
                        )
                    )

            diagnosis = canonical_diagnosis(signal)
            if diagnosis:
                key = ("diagnosis", diagnosis, quote)
                if key not in seen:
                    seen.add(key)
                    refs.append(
                        self._make_ref(
                            document,
                            page_number,
                            "diagnosis",
                            diagnosis,
                            quote,
                            0.85,
                        )
                    )

            role = provider_role(signal)
            if role:
                key = ("provider_role", role, quote)
                if key not in seen:
                    seen.add(key)
                    refs.append(
                        self._make_ref(
                            document,
                            page_number,
                            "provider_role",
                            role,
                            quote,
                            0.85,
                        )
                    )

            role = specialist_role(signal)
            if role:
                key = ("criterion_specialist", role, quote)
                if key not in seen:
                    seen.add(key)
                    refs.append(
                        self._make_ref(
                            document,
                            page_number,
                            "criterion_specialist",
                            role,
                            quote,
                            0.85,
                        )
                    )
                specialist_key = ("specialist_status", "documented", quote)
                if specialist_key not in seen:
                    seen.add(specialist_key)
                    refs.append(
                        self._make_ref(
                            document,
                            page_number,
                            "specialist_status",
                            "documented",
                            quote,
                            0.85,
                        )
                    )

            if document.document_type is not DocumentCategory.DENIAL_LETTER:
                status = step_therapy_status(signal)
                if status in {
                    "failed",
                    "refused",
                    "never_started",
                    "in_progress",
                    "absent",
                    "intolerance",
                }:
                    key = ("step_therapy_status", status, quote)
                    if key not in seen:
                        seen.add(key)
                        refs.append(
                            self._make_ref(
                                document,
                                page_number,
                                "step_therapy_status",
                                status,
                                quote,
                                0.85,
                            )
                        )
                    if status == "failed":
                        criterion_value = _step_therapy_failure_phrase(quote)
                        key = ("criterion_step_therapy", criterion_value, quote)
                        if key not in seen:
                            seen.add(key)
                            refs.append(
                                self._make_ref(
                                    document,
                                    page_number,
                                    "criterion_step_therapy",
                                    criterion_value,
                                    quote,
                                    0.85,
                                )
                            )

            polarity = tb_result_polarity(signal)
            if polarity in {"positive", "negative", "pending", "indeterminate", "absent"}:
                key = ("tb_screen_result", polarity, quote)
                if key not in seen:
                    seen.add(key)
                    refs.append(
                        self._make_ref(
                            document,
                            page_number,
                            "tb_screen_result",
                            polarity,
                            quote,
                            0.9,
                        )
                    )
                if polarity == "negative":
                    criterion_value = "negative TB result"
                    key = ("criterion_tb_screen", criterion_value, quote)
                    if key not in seen:
                        seen.add(key)
                        refs.append(
                            self._make_ref(
                                document,
                                page_number,
                                "criterion_tb_screen",
                                criterion_value,
                                quote,
                                0.85,
                            )
                        )

        return refs

    @staticmethod
    def _unique(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for i in items:
            iu = i.upper()
            if iu not in seen:
                seen.add(iu)
                out.append(iu)
        return out

    @staticmethod
    def _find_line(lines: list[str], needles: tuple[str, ...]) -> str | None:
        for line in lines:
            low = line.lower()
            if any(n in low for n in needles):
                return line.strip()
        return None

    @staticmethod
    def _prior_auth_status(text: str) -> str | None:
        low = text.lower()
        missing_patterns = (
            "no prior authorization",
            "without prior authorization",
            "prior authorization was not obtained",
            "prior authorization not obtained",
            "prior auth was not obtained",
            "prior auth not obtained",
            "missing prior authorization",
            "missing pa number",
            "no pa number",
            "pa number was not provided",
            "authorization number was not provided",
            "authorization not on file",
            "no authorization on file",
            "not authorized before service",
        )
        if any(pattern in low for pattern in missing_patterns):
            return "missing"

        present_patterns = (
            "prior authorization number",
            "prior auth number",
            "pa number",
            "pa #",
            "authorization number",
            "auth number",
            "authorization on file",
        )
        if any(pattern in low for pattern in present_patterns):
            return "documented"
        return None

    @staticmethod
    def _claim_denial_reason(reason: str) -> str | None:
        low = reason.lower()
        missing_cues = (
            "no ",
            "not obtained",
            "missing",
            "not on file",
            "not provided",
            "without",
        )
        if (
            "prior authorization" in low
            or "prior auth" in low
            or "pa number" in low
        ) and any(cue in low for cue in missing_cues):
            return "missing prior authorization"
        if "authorization number" in low and any(cue in low for cue in missing_cues):
            return "missing prior authorization number"
        return None

    @staticmethod
    def _detect_decision(text: str) -> str | None:
        for m in re.finditer(
            rf"\b(?:request\s+status|status|decision|determination){_SEP}([^\n]+)",
            text,
            re.IGNORECASE,
        ):
            decision = EvidenceExtractor._decision_from_value(m.group(1))
            if decision:
                return decision

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
        return None

    @staticmethod
    def _decision_from_value(value: str) -> str | None:
        s = _clean(value).lower()
        if any(k in s for k in ("pending", "in review", "under review")):
            return "pending"
        if "partial" in s:
            return "partial"
        if "deni" in s:
            return "denied"
        if "approv" in s or "authoriz" in s:
            return "approved"
        return None

    @staticmethod
    def _denial_reason(text: str) -> str | None:
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
        m = re.search(
            r"(?:rationale|reason(?:\s+for\s+denial)?)\s*:\s*([^\n]+)",
            text,
            re.IGNORECASE,
        )
        return m.group(1).strip() if m else None


def _merge_section_label(section_type: str, label: str | None) -> str:
    """Prefix an evidence label with the derived document section."""
    if label:
        return f"{section_type}: {label}"
    return section_type
