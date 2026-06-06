"""ClaudeEvidenceExtractor: AI evidence extraction with an anti-fabrication gate.

Flow (AI backend available):
    document text (+ OCR text)
        -> Claude prompt asking for evidence items as JSON
        -> parse + validate each item
        -> ANTI-FABRICATION GATE: the item's quoted_text must appear verbatim
           in the source; otherwise the item is dropped (never trusted)
        -> EvidenceReference list

When no AI backend is configured (offline local backend) or the AI call fails,
the extractor falls back to the deterministic :class:`EvidenceExtractor`, so the
EvidenceReference contract and traceability hold either way.

Safety: nothing is fabricated. A reference is only emitted if its quote is
present in the document, guaranteeing every fact is genuinely source-backed.
"""

from __future__ import annotations

import json
import json
import re

from app.evidence.extractor import EvidenceExtractor
from app.models.case_document import CaseDocument
from app.models.evidence_reference import EvidenceReference
from app.services.factory import get_llm_client
from app.services.json_utils import extract_json_payload
from app.services.llm_client import LLMClient, LLMError


EVIDENCE_EXTRACTION_SYSTEM_PROMPT = """\
You are a meticulous clinical evidence extractor for prior-authorization review.
You read a medical document (it may be OCR text from a scan) and extract
source-backed facts.

Strict rules:
1. Extract only facts that are explicitly present in the document.
2. For EVERY fact you MUST include "quoted_text" copied VERBATIM from the
document - the exact substring that supports the fact. Never paraphrase the
quote. If you cannot quote it, do not include it.
3. NEVER invent, infer, or guess values. No quote => no fact.
4. Provide a "confidence" between 0.0 and 1.0 reflecting how clearly the
document supports the fact.
5. Use these fact_type values when applicable: patient_name, member_id,
date_of_birth, diagnosis, requested_service, insurance_company, physician_name,
decision, denial_reason, icd10_codes, cpt_codes. Use "other" otherwise.
6. Output VALID JSON ONLY: an object with an "evidence" array. Each item has:
   fact_type, value, quoted_text, page_number, confidence. No commentary.\
"""


def _normalize_quote(text: str) -> str:
    """Lowercase + collapse whitespace for tolerant verbatim matching."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


class ClaudeEvidenceExtractor:
    """Extract EvidenceReference objects with Claude + an anti-fabrication gate."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        max_tokens: int = 1800,
        max_retries: int = 2,
    ) -> None:
        self.llm = llm_client or get_llm_client()
        self.max_tokens = max_tokens
        self.max_retries = max(1, max_retries)
        self._fallback = EvidenceExtractor()

    @property
    def backend_name(self) -> str:
        return getattr(self.llm, "name", "unknown")

    @property
    def used_ai(self) -> bool:
        return getattr(self.llm, "is_ai", False)

    def extract(
        self,
        document: CaseDocument,
        ocr_text: str | None = None,
    ) -> list[EvidenceReference]:
        """Extract evidence references for a document.

        Args:
            document: The CaseDocument (provides id, filename, page text).
            ocr_text: Optional OCR text to use instead of the stored raw_text
                (e.g. when the document is image-only).
        """
        # Offline / non-AI backend: deterministic extractor (already safe).
        if not self.used_ai:
            return self._fallback.extract(document)

        pages = document.pages()
        if ocr_text:
            # Treat OCR text as the authoritative page source if provided.
            pages = ocr_text.split("\f") if "\f" in ocr_text else [ocr_text]

        full_source = "\n".join(pages)
        messages = [
            {"role": "user", "content": self._build_prompt(full_source)}
        ]

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.llm.complete(
                    system=EVIDENCE_EXTRACTION_SYSTEM_PROMPT,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=0.0,
                )
                items = self._parse(response.text)
                refs = self._to_references(document, items, pages)
                # If the model produced nothing usable, fall back so we never
                # silently lose evidence.
                if not refs:
                    return self._fallback.extract(document)
                return refs
            except (ValueError, json.JSONDecodeError):
                if attempt < self.max_retries:
                    messages = messages + [
                        {
                            "role": "user",
                            "content": (
                                "Your previous response was not valid JSON with "
                                "an 'evidence' array. Respond again with VALID "
                                "JSON ONLY. No commentary."
                            ),
                        }
                    ]
                    continue
            except LLMError:
                break  # backend failure -> deterministic fallback

        return self._fallback.extract(document)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_prompt(source: str) -> str:
        schema = {
            "evidence": [
                {
                    "fact_type": "diagnosis",
                    "value": "Rheumatoid arthritis",
                    "quoted_text": "Diagnosis: Rheumatoid arthritis",
                    "page_number": 1,
                    "confidence": 0.95,
                }
            ]
        }
        return (
            "Extract source-backed evidence from the document below. Return JSON "
            "with an 'evidence' array; each item must include verbatim "
            "quoted_text copied from the document.\n\n"
            f"Schema example:\n{json.dumps(schema, indent=2)}\n\n"
            "--- BEGIN DOCUMENT ---\n"
            f"{source}\n"
            "--- END DOCUMENT ---\n"
        )

    @staticmethod
    def _parse(text: str) -> list[dict]:
        # Uses the shared JSON payload extractor (Milestone 12 de-duplication).
        # Behavior preserved: accept an object with an 'evidence' array, or a
        # top-level array; raise ValueError otherwise.
        payload = extract_json_payload(text)
        if isinstance(payload, dict) and isinstance(payload.get("evidence"), list):
            return payload["evidence"]
        if isinstance(payload, list):
            return payload
        raise ValueError("JSON did not contain an 'evidence' array.")

    def _to_references(
        self,
        document: CaseDocument,
        items: list[dict],
        pages: list[str],
    ) -> list[EvidenceReference]:
        """Convert validated items to references, enforcing the quote gate."""
        normalized_pages = [_normalize_quote(p) for p in pages]
        full_norm = _normalize_quote("\n".join(pages))
        refs: list[EvidenceReference] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quoted_text", "")).strip()
            value = str(item.get("value", "")).strip()
            fact_type = str(item.get("fact_type", "other")).strip() or "other"
            if not quote or not value:
                continue  # must be source-backed

            # ANTI-FABRICATION GATE: the quote must appear in the source.
            norm_quote = _normalize_quote(quote)
            if norm_quote not in full_norm:
                continue  # reject fabricated / non-verbatim quotes

            # Resolve the page the quote actually appears on.
            page_number = self._resolve_page(norm_quote, normalized_pages,
                                              item.get("page_number"))
            try:
                confidence = float(item.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            confidence = max(0.0, min(1.0, confidence))

            refs.append(
                EvidenceReference(
                    case_id=document.case_id,
                    source_document_id=document.document_id,
                    source_filename=document.filename,
                    page_number=page_number,
                    section_label=self._section_label(quote),
                    quoted_text=quote,
                    normalized_fact=f"{fact_type}: {value}",
                    fact_type=fact_type,
                    confidence_score=confidence,
                )
            )
        return refs

    @staticmethod
    def _resolve_page(norm_quote: str, normalized_pages: list[str], claimed) -> int:
        for idx, page in enumerate(normalized_pages, start=1):
            if norm_quote in page:
                return idx
        try:
            p = int(claimed)
            if p >= 1:
                return p
        except (TypeError, ValueError):
            pass
        return 1

    @staticmethod
    def _section_label(quote: str) -> str | None:
        m = re.match(r"\s*([A-Za-z0-9 /#()\-]+?)\s*[:.]", quote)
        return m.group(1).strip() if m else None
