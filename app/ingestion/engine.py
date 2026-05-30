"""DocumentIngestionEngine: detect file type, decide on OCR, route.

Flow:
    bytes + filename
        -> detect kind (TEXT / SEARCHABLE_PDF / SCANNED_PDF / IMAGE)
        -> if a usable text layer exists, use it (no OCR)
        -> else OCR via the configured provider (page by page)
        -> return per-page text + OCR results + classification

The engine never fabricates text. When OCR is required but unavailable, it
returns empty page text, an explanatory warning, and ``ocr_available=False`` so
the caller can surface the problem rather than crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.ingestion.classifier import DocumentClassifier
from app.models.case_document import DocumentCategory
from app.models.ocr_result import OCRPageResult, ProcessingMethod
from app.ocr.base import OCRNotAvailableError
from app.ocr.providers import OCRProvider, get_ocr_provider

# Extension groups.
IMAGE_EXTS = {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "gif"}
PDF_EXTS = {"pdf"}
TEXT_EXTS = {"txt"}

# A PDF page with at least this many non-whitespace chars is "searchable".
_TEXT_LAYER_MIN_CHARS = 20


class IngestionKind(str, Enum):
    """How a document was classified for ingestion routing."""

    TEXT = "TEXT"
    SEARCHABLE_PDF = "SEARCHABLE_PDF"
    SCANNED_PDF = "SCANNED_PDF"
    IMAGE = "IMAGE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass
class IngestionResult:
    """Outcome of ingesting one document."""

    filename: str
    kind: IngestionKind
    pages: list[str] = field(default_factory=list)
    ocr_results: list[OCRPageResult] = field(default_factory=list)
    document_category: DocumentCategory = DocumentCategory.OTHER
    ocr_used: bool = False
    ocr_available: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def full_text(self) -> str:
        return "\f".join(self.pages)

    @property
    def mean_confidence(self) -> float:
        if not self.ocr_results:
            return 1.0  # text layer / no OCR -> treated as fully reliable
        vals = [r.confidence for r in self.ocr_results]
        return round(sum(vals) / len(vals), 4)

    def low_confidence_pages(self, threshold: float) -> list[int]:
        return [
            r.page_number for r in self.ocr_results if r.confidence < threshold
        ]


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


class DocumentIngestionEngine:
    """Detect type, decide on OCR, and route a document to page text."""

    def __init__(
        self,
        ocr_provider: OCRProvider | None = None,
        classifier: DocumentClassifier | None = None,
    ) -> None:
        self.ocr = ocr_provider or get_ocr_provider()
        self.classifier = classifier or DocumentClassifier()

    # ------------------------------------------------------------------ #
    # Detection
    # ------------------------------------------------------------------ #
    def detect_kind(self, filename: str, data: bytes) -> IngestionKind:
        """Detect the ingestion kind from extension + (for PDFs) text layer."""
        ext = _ext(filename)
        if ext in TEXT_EXTS:
            return IngestionKind.TEXT
        if ext in IMAGE_EXTS:
            return IngestionKind.IMAGE
        if ext in PDF_EXTS:
            return (
                IngestionKind.SEARCHABLE_PDF
                if self._pdf_has_text_layer(data)
                else IngestionKind.SCANNED_PDF
            )
        return IngestionKind.UNSUPPORTED

    @staticmethod
    def _pdf_has_text_layer(data: bytes) -> bool:
        """True if the PDF has a usable embedded text layer."""
        try:
            import fitz
        except ImportError:  # pragma: no cover - fitz is a core dep
            return False
        try:
            with fitz.open(stream=data, filetype="pdf") as doc:
                total = 0
                for page in doc:
                    total += len(page.get_text().strip())
                    if total >= _TEXT_LAYER_MIN_CHARS:
                        return True
        except Exception:  # noqa: BLE001 - treat unreadable PDFs as scanned
            return False
        return False

    @staticmethod
    def _pdf_text_pages(data: bytes) -> list[str]:
        import fitz

        with fitz.open(stream=data, filetype="pdf") as doc:
            return [page.get_text() for page in doc] or [""]

    # ------------------------------------------------------------------ #
    # Ingestion
    # ------------------------------------------------------------------ #
    def ingest(
        self,
        filename: str,
        data: bytes,
        *,
        document_id: str,
        case_id: str | None = None,
        category_override: DocumentCategory | str | None = None,
    ) -> IngestionResult:
        """Ingest a document into page text + OCR metadata + classification."""
        kind = self.detect_kind(filename, data)
        result = IngestionResult(filename=filename, kind=kind)

        if kind is IngestionKind.UNSUPPORTED:
            result.warnings.append(
                f"Unsupported file type for '{filename}'. Supported: TXT, PDF, "
                "PNG, JPG, JPEG."
            )
            result.ocr_available = self.ocr.is_available
            return result

        if kind is IngestionKind.TEXT:
            text = self._decode(data)
            result.pages = [text]

        elif kind is IngestionKind.SEARCHABLE_PDF:
            result.pages = self._pdf_text_pages(data)

        else:  # SCANNED_PDF or IMAGE -> OCR
            result.ocr_available = self.ocr.is_available
            try:
                if kind is IngestionKind.IMAGE:
                    ocr_results = self.ocr.ocr_image(
                        data, document_id=document_id, case_id=case_id, filename=filename
                    )
                else:
                    ocr_results = self.ocr.ocr_pdf(
                        data, document_id=document_id, case_id=case_id, filename=filename
                    )
                result.ocr_results = ocr_results
                result.pages = [r.raw_text for r in ocr_results]
                result.ocr_used = True
                if not any(p.strip() for p in result.pages):
                    result.warnings.append(
                        "OCR produced no text; the document may be blank or "
                        "unreadable. No evidence will be extracted."
                    )
            except OCRNotAvailableError as exc:
                result.ocr_available = False
                result.pages = [""]
                result.warnings.append(
                    f"OCR unavailable for '{filename}': {exc} "
                    "The document was accepted but no text was extracted; "
                    "install OCR to process scanned documents."
                )

        # Classify from the (possibly OCR'd) text.
        result.document_category = self.classifier.classify(
            filename, result.full_text, override=category_override
        )
        return result

    @staticmethod
    def _decode(data: bytes) -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1", errors="replace")
