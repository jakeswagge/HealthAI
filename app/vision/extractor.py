"""VisionEvidenceExtractor: OCR page results -> EvidenceReference objects.

Reuses the deterministic per-page field detection from the text-based
:class:`EvidenceExtractor` (no logic duplication), but operates on
:class:`OCRPageResult` pages and:

- preserves the OCR ``page_number`` and ``document`` on each reference,
- blends the OCR page confidence into each reference's confidence
  (final = field_confidence * ocr_confidence), so low-quality OCR yields
  low-confidence evidence (never silently trusted),
- records the processing method in the evidence ``section_label`` context via
  the normalized fact (the OCR method is also available on the OCRPageResult).

The output is ordinary ``EvidenceReference`` objects, so OCR-derived evidence
participates in conflict detection, assembly, authoritative facts, and
reviewer resolution through the existing workflows unchanged.
"""

from __future__ import annotations

from app.evidence.extractor import EvidenceExtractor
from app.models.case_document import CaseDocument
from app.models.evidence_reference import EvidenceReference
from app.models.ocr_result import OCRPageResult


class VisionEvidenceExtractor:
    """Extract evidence from OCR page results, preserving traceability."""

    def __init__(self, base_extractor: EvidenceExtractor | None = None) -> None:
        self._extractor = base_extractor or EvidenceExtractor()

    def extract(
        self,
        document: CaseDocument,
        ocr_pages: list[OCRPageResult],
    ) -> list[EvidenceReference]:
        """Extract evidence references from a document's OCR page results.

        Args:
            document: The CaseDocument the OCR belongs to (for ids/filename).
            ocr_pages: Page-level OCR results (1 per page).

        Returns:
            EvidenceReference objects with OCR-blended confidence.
        """
        refs: list[EvidenceReference] = []
        for page in ocr_pages:
            if not page.raw_text.strip():
                continue
            page_refs = self._extractor._extract_page(  # noqa: SLF001 - intentional reuse
                document, page.page_number, page.raw_text
            )
            for ref in page_refs:
                # Blend OCR confidence so OCR uncertainty is never hidden.
                ref.confidence_score = round(
                    ref.confidence_score * page.confidence, 4
                )
                refs.append(ref)
        return refs

    def extract_from_text_pages(
        self,
        document: CaseDocument,
        pages: list[str],
        ocr_confidence: float = 1.0,
    ) -> list[EvidenceReference]:
        """Convenience: extract from raw page strings with a flat OCR confidence."""
        refs: list[EvidenceReference] = []
        for page_number, text in enumerate(pages, start=1):
            if not text.strip():
                continue
            page_refs = self._extractor._extract_page(  # noqa: SLF001
                document, page_number, text
            )
            for ref in page_refs:
                ref.confidence_score = round(ref.confidence_score * ocr_confidence, 4)
                refs.append(ref)
        return refs
