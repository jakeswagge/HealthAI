"""DocumentClassifier: assign a DocumentCategory to a document.

Wraps the existing keyword-based ``classify_document`` heuristic (from
``app.models.case_document``) and adds explicit manual-override support, so the
ingestion flow can auto-classify while still letting a human force a category.
This keeps a single source of truth for classification logic.
"""

from __future__ import annotations

from app.models.case_document import (
    PAGE_DELIMITER,
    CaseDocument,
    DocumentCategory,
    DocumentSection,
    DocumentSectionType,
    classify_document,
)


_SECTION_HINTS: tuple[tuple[DocumentSectionType, tuple[str, ...]], ...] = (
    (
        DocumentSectionType.CLAIM_OR_DENIAL,
        (
            "claim denial",
            "coverage denial",
            "reason for denial",
            "adverse determination",
            "remittance advice",
            "explanation of benefits",
            "eob",
            "no prior authorization",
            "pa number",
            "authorization number",
        ),
    ),
    (
        DocumentSectionType.PAYER_POLICY,
        (
            "medical policy",
            "coverage criteria",
            "clinical criteria",
            "local coverage determination",
            "lcd",
            "prior authorization criteria",
        ),
    ),
    (
        DocumentSectionType.LABS,
        (
            "laboratory",
            "lab result",
            "reference range",
            "specimen",
            "quantiferon",
            "t-spot",
            "ppd",
        ),
    ),
    (
        DocumentSectionType.IMAGING,
        (
            "radiology",
            "imaging report",
            "mri",
            "ct scan",
            "x-ray",
            "ultrasound",
        ),
    ),
    (
        DocumentSectionType.PROCEDURE_NOTE,
        (
            "operative report",
            "procedure note",
            "surgery note",
            "treatment date",
            "procedure performed",
        ),
    ),
    (
        DocumentSectionType.CLINICAL_HISTORY,
        (
            "history of present illness",
            "clinical note",
            "progress note",
            "assessment and plan",
            "h&p",
            "office visit",
            "past medical history",
            "methotrexate",
            "rheumatology",
        ),
    ),
    (
        DocumentSectionType.ADMINISTRATIVE,
        (
            "patient name",
            "member id",
            "subscriber id",
            "date of birth",
            "provider name",
            "requesting provider",
            "insurance company",
        ),
    ),
)

_BOUNDARY_HINTS = (
    "document type:",
    "section:",
    "page 1 of",
    "claim denial",
    "clinical note",
    "laboratory report",
    "imaging report",
    "operative report",
    "medical policy",
    "explanation of benefits",
)


class DocumentClassifier:
    """Classify documents into a :class:`DocumentCategory`."""

    def classify(
        self,
        filename: str | None,
        text: str | None,
        override: DocumentCategory | str | None = None,
    ) -> DocumentCategory:
        """Return the document category.

        Args:
            filename: Original filename (filename hints take precedence).
            text: Document text (content hints used if filename is ambiguous).
            override: Optional manual override that wins over auto-detection.
        """
        if override is not None:
            if isinstance(override, DocumentCategory):
                return override
            try:
                return DocumentCategory(str(override).strip().upper())
            except ValueError:
                return DocumentCategory.OTHER
        return classify_document(filename, text)

    def classify_section(
        self,
        text: str | None,
        *,
        filename: str | None = None,
        document_type: DocumentCategory | None = None,
    ) -> tuple[DocumentSectionType, float]:
        """Classify one page/section into a semantic section type."""
        haystack = f"{filename or ''}\n{text or ''}".lower()
        for section_type, hints in _SECTION_HINTS:
            if any(hint in haystack for hint in hints):
                return section_type, 0.8

        if document_type is DocumentCategory.DENIAL_LETTER:
            return DocumentSectionType.CLAIM_OR_DENIAL, 0.6
        if document_type is DocumentCategory.CLINICAL_NOTE:
            return DocumentSectionType.CLINICAL_HISTORY, 0.6
        if document_type is DocumentCategory.LAB_RESULT:
            return DocumentSectionType.LABS, 0.6
        if document_type is DocumentCategory.IMAGING_REPORT:
            return DocumentSectionType.IMAGING, 0.6
        if document_type is DocumentCategory.PRIOR_AUTH_FORM:
            return DocumentSectionType.ADMINISTRATIVE, 0.6
        return DocumentSectionType.OTHER, 0.4

    def detect_sections(self, document: CaseDocument) -> list[DocumentSection]:
        """Derive page-range sections from a document without persisting them."""
        pages = document.pages()
        sections: list[DocumentSection] = []
        current_type: DocumentSectionType | None = None
        current_conf = 0.0
        current_start = 1
        current_pages: list[str] = []

        for page_number, page_text in enumerate(pages, start=1):
            section_type, confidence = self.classify_section(
                page_text,
                filename=document.filename,
                document_type=document.document_type,
            )
            starts_boundary = _looks_like_boundary(page_text)
            if current_pages and (
                section_type != current_type or (starts_boundary and page_number > current_start)
            ):
                sections.append(
                    _section(
                        document,
                        current_start,
                        page_number - 1,
                        current_type or DocumentSectionType.OTHER,
                        current_pages,
                        current_conf,
                    )
                )
                current_start = page_number
                current_pages = []

            current_type = section_type
            current_conf = max(current_conf, confidence)
            current_pages.append(page_text)

        if current_pages:
            sections.append(
                _section(
                    document,
                    current_start,
                    len(pages),
                    current_type or DocumentSectionType.OTHER,
                    current_pages,
                    current_conf,
                )
            )
        return sections


def _looks_like_boundary(text: str | None) -> bool:
    first_lines = "\n".join((text or "").splitlines()[:5]).lower()
    return any(hint in first_lines for hint in _BOUNDARY_HINTS)


def _section(
    document: CaseDocument,
    page_start: int,
    page_end: int,
    section_type: DocumentSectionType,
    pages: list[str],
    confidence: float,
) -> DocumentSection:
    return DocumentSection(
        section_id=f"SEC-{document.document_id}-{page_start}-{page_end}",
        case_id=document.case_id,
        document_id=document.document_id,
        section_type=section_type,
        page_start=page_start,
        page_end=page_end,
        text=PAGE_DELIMITER.join(pages),
        confidence_score=confidence,
    )


def detect_document_sections(document: CaseDocument) -> list[DocumentSection]:
    """Convenience wrapper used by extraction/assembly paths."""
    return DocumentClassifier().detect_sections(document)
