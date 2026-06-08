"""IngestionService: document attachment, intelligent ingestion, and OCR.

Extracted from ``CaseService`` during the Milestone 12 facade decomposition.
Owns the "get bytes/text into a case" concern: plain document attachment
(Milestone 6/7) and OCR-aware ingestion of scanned PDFs/images (Milestone 9).
Behavior is identical to the original CaseService methods - this is a cohesion
extraction, not a logic change.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.audit.repository import AuditRepository
from app.cases.document_repository import CaseDocumentRepository
from app.cases.lifecycle import CaseLifecycle
from app.ingestion.engine import DocumentIngestionEngine, IngestionResult
from app.ocr.repository import OCRResultRepository
from app.ocr.providers import OCRReadiness, describe_ocr_provider, ocr_readiness
from app.models.audit_event import AuditActor, AuditEventType
from app.models.case_document import CaseDocument, DocumentCategory, classify_document
from app.models.ocr_result import DEFAULT_OCR_CONFIDENCE_THRESHOLD, OCRPageResult


@dataclass(frozen=True)
class DocumentOCRStatus:
    """Derived OCR status for one stored document."""

    document_id: str
    filename: str
    status: str
    detail: str
    ocr_pages: int
    processing_method: str


_IMAGE_EXTS = {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "gif"}


class IngestionService:
    """Attach documents to cases and ingest scanned/image uploads via OCR."""

    def __init__(
        self,
        lifecycle: CaseLifecycle,
        documents: CaseDocumentRepository,
        ocr_results: OCRResultRepository,
        ingestion: DocumentIngestionEngine,
        audit: AuditRepository,
    ) -> None:
        self.lifecycle = lifecycle
        self.documents = documents
        self.ocr_results = ocr_results
        self.ingestion = ingestion
        self.audit = audit

    # ------------------------------------------------------------------ #
    # Plain document attachment (Milestone 6/7)
    # ------------------------------------------------------------------ #
    def add_document(
        self,
        case_id: str,
        filename: str,
        raw_text: str,
        page_count: int = 1,
        document_type: DocumentCategory | str | None = None,
    ) -> CaseDocument:
        """Attach a supporting document to a case and record an audit event.

        The document type is auto-classified from filename/content when not
        explicitly provided.
        """
        self.lifecycle.require(case_id)
        if document_type is None:
            document_type = classify_document(filename, raw_text)
        document = CaseDocument(
            case_id=case_id,
            filename=filename,
            document_type=document_type,
            page_count=page_count,
            raw_text=raw_text,
        )
        self.documents.add(document)
        self.audit.log(
            case_id,
            AuditEventType.DOCUMENT_UPLOADED,
            details=f"Document added: {filename} ({document.document_type.value}).",
            actor=AuditActor.USER,
        )
        return document

    def list_documents(self, case_id: str) -> list[CaseDocument]:
        return self.documents.for_case(case_id)

    # ------------------------------------------------------------------ #
    # Intelligent ingestion + OCR (Milestone 9)
    # ------------------------------------------------------------------ #
    def ingest_document(
        self,
        case_id: str,
        filename: str,
        data: bytes,
        category_override: DocumentCategory | str | None = None,
        ocr_confidence_threshold: float = DEFAULT_OCR_CONFIDENCE_THRESHOLD,
    ) -> tuple[CaseDocument, IngestionResult]:
        """Ingest any supported upload (TXT/PDF/PNG/JPG/JPEG) into a case.

        Detects whether OCR is needed, runs it page-by-page when so, stores the
        document (page text joined by the page delimiter) plus the per-page OCR
        results, classifies the document, and records audit events. Low-OCR-
        confidence pages produce a warning audit entry; nothing is silently
        accepted.
        """
        self.lifecycle.require(case_id)
        document = CaseDocument(
            case_id=case_id,
            filename=filename,
            document_type=DocumentCategory.OTHER,  # set after ingestion
            page_count=1,
            raw_text="",
        )

        result = self.ingestion.ingest(
            filename,
            data,
            document_id=document.document_id,
            case_id=case_id,
            category_override=category_override,
        )

        # Finalize the document from the ingestion output.
        document.document_type = result.document_category
        document.page_count = max(1, result.page_count)
        document.raw_text = "\f".join(result.pages) if result.pages else ""
        self.documents.add(document)

        # Persist OCR results (if any) and stamp the case_id.
        for r in result.ocr_results:
            r.case_id = case_id
        if result.ocr_results:
            self.ocr_results.add_many(result.ocr_results)

        # Audit: document added (with ingestion kind + method).
        method = (
            result.ocr_results[0].processing_method.value
            if result.ocr_results
            else ("TEXT_LAYER" if result.kind.value != "TEXT" else "TEXT")
        )
        self.audit.log(
            case_id,
            AuditEventType.CASE_DOCUMENT_ADDED,
            details=(
                f"Ingested '{filename}' as {result.kind.value} "
                f"(type={document.document_type.value}, pages={document.page_count}, "
                f"method={method}, ocr_used={result.ocr_used})."
            ),
            actor=AuditActor.USER,
        )

        # Quality gate: warn (audit) on unavailable OCR or low-confidence pages.
        if not result.ocr_available:
            self.audit.log(
                case_id,
                AuditEventType.STATUS_CHANGED,
                details=f"OCR unavailable for '{filename}'; no text extracted.",
                actor=AuditActor.SYSTEM,
            )
        low_pages = result.low_confidence_pages(ocr_confidence_threshold)
        if low_pages:
            self.audit.log(
                case_id,
                AuditEventType.STATUS_CHANGED,
                details=(
                    f"Low-confidence OCR on '{filename}' pages {low_pages} "
                    f"(< {ocr_confidence_threshold:.0%}); flagged for reviewer "
                    "inspection."
                ),
                actor=AuditActor.SYSTEM,
            )
        for w in result.warnings:
            self.audit.log(
                case_id,
                AuditEventType.STATUS_CHANGED,
                details=f"Ingestion warning for '{filename}': {w}",
                actor=AuditActor.SYSTEM,
            )

        return document, result

    def list_ocr_results(self, case_id: str) -> list[OCRPageResult]:
        return self.ocr_results.for_case(case_id)

    def ocr_results_for_document(self, document_id: str) -> list[OCRPageResult]:
        return self.ocr_results.for_document(document_id)

    def describe_ocr(self) -> str:
        return describe_ocr_provider(self.ingestion.ocr)

    def ocr_readiness(self) -> OCRReadiness:
        return ocr_readiness(self.ingestion.ocr)

    def document_ocr_statuses(self, case_id: str) -> list[DocumentOCRStatus]:
        """Return reviewer-facing OCR provenance for every document in a case."""
        readiness = self.ocr_readiness()
        statuses: list[DocumentOCRStatus] = []
        for document in self.list_documents(case_id):
            pages = self.ocr_results_for_document(document.document_id)
            statuses.append(
                _document_ocr_status(document, pages, readiness)
            )
        return statuses


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _document_ocr_status(
    document: CaseDocument,
    pages: list[OCRPageResult],
    readiness: OCRReadiness,
) -> DocumentOCRStatus:
    if pages:
        method = pages[0].processing_method.value
        if method == "MOCK":
            status = "Mock OCR used"
            detail = "Mock OCR is test-only and does not validate scanned-document support."
        else:
            status = "OCR used"
            detail = "Page text was produced by the active OCR provider."
        return DocumentOCRStatus(
            document_id=document.document_id,
            filename=document.filename,
            status=status,
            detail=detail,
            ocr_pages=len(pages),
            processing_method=method,
        )

    ext = _ext(document.filename)
    if ext == "txt":
        status = "TXT files do not use OCR"
        detail = "Stored text was decoded directly from the TXT upload."
        method = "TEXT"
    elif ext == "pdf" and document.raw_text.strip():
        status = "Text layer used"
        detail = "This PDF had embedded text, so OCR was skipped."
        method = "TEXT_LAYER"
    elif ext in _IMAGE_EXTS or ext == "pdf":
        status = "OCR unavailable" if not readiness.is_available else "No OCR rows"
        detail = (
            readiness.message
            if not readiness.is_available
            else "No persisted OCR output exists for this document."
        )
        method = "NONE"
    else:
        status = "OCR not applicable"
        detail = "This document type was stored without OCR output."
        method = "NONE"

    return DocumentOCRStatus(
        document_id=document.document_id,
        filename=document.filename,
        status=status,
        detail=detail,
        ocr_pages=0,
        processing_method=method,
    )
