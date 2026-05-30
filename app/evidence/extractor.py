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

from app.models.case_document import CaseDocument
from app.models.evidence_reference import EvidenceReference

# Logical fact types the extractor knows how to find.
FACT_TYPES: tuple[str, ...] = (
    "patient_name",
    "member_id",
    "date_of_birth",
    "diagnosis",
    "requested_service",
    "insurance_company",
    "physician_name",
    "decision",
    "denial_reason",
    "icd10_codes",
    "cpt_codes",
)

_SEP = r"\s*[:.]+\s*"

ICD10_RE = re.compile(r"\b([A-TV-Z][0-9][0-9AB](?:\.[0-9A-Z]{1,4})?)\b")
CPT_RE = re.compile(r"\b(\d{5})\b")

# Single-line "label: value" patterns. Group 1 = value.
_LINE_PATTERNS: dict[str, str] = {
    "patient_name": rf"(?:member\s+name|patient\s+name|patient|member){_SEP}(.+)",
    "member_id": rf"\b(?:member\s*id|member\s*#|subscriber\s*id|id\s*#){_SEP}([A-Z0-9][A-Z0-9\-]+)",
    "date_of_birth": rf"(?:date\s+of\s+birth|dob){_SEP}([0-9]{{1,2}}/[0-9]{{1,2}}/[0-9]{{2,4}})",
    "diagnosis": rf"(?:diagnosis|dx){_SEP}(.+)",
    "requested_service": rf"(?:procedure|requested\s+service|service){_SEP}(.+)",
    "insurance_company": rf"(?:payer|insurance\s+company|health\s+plan|insurer){_SEP}(.+)",
    "physician_name": rf"(?:requesting\s+provider|ordering\s+provider|requesting\s+physician|physician|provider){_SEP}(.+)",
}


def _clean(value: str) -> str:
    value = value.splitlines()[0].strip()
    return re.sub(r"\s{2,}", " ", value).strip(" .")


def _section_label(line: str) -> str | None:
    """Return the label portion (before the colon) of a 'label: value' line."""
    m = re.match(r"\s*([A-Za-z0-9 /#()\-]+?)\s*[:.]", line)
    return m.group(1).strip() if m else None


class EvidenceExtractor:
    """Produce :class:`EvidenceReference` objects from a document."""

    def extract(self, document: CaseDocument) -> list[EvidenceReference]:
        """Extract all evidence references found in the document."""
        evidence: list[EvidenceReference] = []
        pages = document.pages()

        for page_index, page_text in enumerate(pages, start=1):
            evidence.extend(self._extract_page(document, page_index, page_text))

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

        # --- single-line label/value facts ---
        for fact_type, pattern in _LINE_PATTERNS.items():
            for line in lines:
                m = re.search(pattern, line, re.IGNORECASE)
                if not m:
                    continue
                value = _clean(m.group(1))
                if fact_type == "diagnosis":
                    # Strip an embedded leading ICD-10 code from the value.
                    value = re.sub(
                        r"^[A-TV-Z][0-9][0-9AB](?:\.[0-9A-Z]{1,4})?\s*\(?",
                        "",
                        value,
                    ).strip(" ()")
                if fact_type == "physician_name":
                    value = re.split(r"\bNPI\b", value, flags=re.IGNORECASE)[0].strip()
                if not value:
                    continue
                refs.append(
                    self._make_ref(document, page_number, fact_type, value, line, 0.9)
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

        # --- code lists ---
        for code in self._unique(ICD10_RE.findall(page_text)):
            line = self._find_line(lines, (code.lower(),)) or code
            refs.append(self._make_ref(document, page_number, "icd10_codes", code, line, 0.8))

        cpt_context = "\n".join(l for l in lines if "cpt" in l.lower() or "hcpcs" in l.lower())
        for code in self._unique(CPT_RE.findall(cpt_context)):
            line = self._find_line(lines, (code,)) or code
            refs.append(self._make_ref(document, page_number, "cpt_codes", code, line, 0.8))

        return refs

    # ------------------------------------------------------------------ #
    # Small helpers
    # ------------------------------------------------------------------ #
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
    def _detect_decision(text: str) -> str | None:
        lowered = text.lower()
        m = re.search(r"status\s*:\s*([a-z ]+)", text, re.IGNORECASE)
        if m:
            s = m.group(1).lower()
            if "partial" in s:
                return "partial"
            if "deni" in s:
                return "denied"
            if "approv" in s:
                return "approved"
        if any(k in lowered for k in ("adverse determination", "denied", "denial", "not medically necessary")):
            return "denied"
        if any(k in lowered for k in ("favorable determination", "approved", "authorized")):
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
