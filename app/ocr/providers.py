"""OCR providers that produce page-level :class:`OCRPageResult` output.

This extends the existing OCR abstraction (``app.ocr.base``) with providers
that return the Milestone-9 page-level result type and support images as well
as PDFs.

Providers:
- :class:`LocalTesseractOCRProvider` - real, offline OCR via ``pytesseract`` +
  ``pdf2image``/PyMuPDF rasterization. Degrades gracefully: if the Tesseract
  binary or its Python bindings are unavailable it reports
  ``is_available = False`` and raises :class:`OCRNotAvailableError` rather than
  crashing the workflow.
- :class:`MockOCRProvider` - deterministic, dependency-free provider used as the
  offline default and for tests. It treats the input bytes as UTF-8/latin-1
  text (mirroring how the document corpus is authored) so the full ingestion ->
  evidence -> assembly pipeline is exercisable without Tesseract installed.

Both never fabricate text beyond what they actually read/decode.
"""

from __future__ import annotations

import abc
from typing import Optional

from app.models.ocr_result import OCRPageResult, ProcessingMethod
from app.ocr.base import OCRCapability, OCRNotAvailableError


class OCRProvider(abc.ABC):
    """Interface for page-level OCR providers."""

    name: str = "abstract-provider"
    processing_method: ProcessingMethod = ProcessingMethod.PLACEHOLDER

    @property
    @abc.abstractmethod
    def is_available(self) -> bool:
        """True if this provider can perform OCR right now."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def capabilities(self) -> set[OCRCapability]:
        raise NotImplementedError

    @abc.abstractmethod
    def ocr_image(
        self, data: bytes, *, document_id: str, case_id: str | None = None,
        filename: str | None = None,
    ) -> list[OCRPageResult]:
        """OCR a single raster image; returns a one-element page list."""
        raise NotImplementedError

    @abc.abstractmethod
    def ocr_pdf(
        self, data: bytes, *, document_id: str, case_id: str | None = None,
        filename: str | None = None,
    ) -> list[OCRPageResult]:
        """OCR every page of a (scanned) PDF; returns one result per page."""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Mock provider (deterministic, offline default)
# --------------------------------------------------------------------------- #
def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


# Page delimiter used by mock multi-page "PDFs" authored as text fixtures.
MOCK_PAGE_DELIMITER = "\f"


class MockOCRProvider(OCRProvider):
    """Deterministic OCR stand-in that decodes input bytes as text.

    It does not call any OCR engine. It is used when no real OCR is available
    so the rest of the pipeline (classification, evidence, assembly, review,
    appeal, audit) remains fully testable offline. Confidence is configurable
    so tests can exercise the low-confidence quality gate.
    """

    name = "mock-ocr"
    processing_method = ProcessingMethod.MOCK

    def __init__(self, confidence: float = 0.95) -> None:
        self._confidence = max(0.0, min(1.0, confidence))

    @property
    def is_available(self) -> bool:
        return True

    @property
    def capabilities(self) -> set[OCRCapability]:
        return {OCRCapability.IMAGE, OCRCapability.SCANNED_PDF}

    def _page(self, text: str, page_number: int, document_id: str,
              case_id: str | None) -> OCRPageResult:
        return OCRPageResult(
            document_id=document_id,
            case_id=case_id,
            page_number=page_number,
            raw_text=text,
            confidence=self._confidence,
            processing_method=self.processing_method,
        )

    def ocr_image(self, data, *, document_id, case_id=None, filename=None):
        return [self._page(_decode(data), 1, document_id, case_id)]

    def ocr_pdf(self, data, *, document_id, case_id=None, filename=None):
        text = _decode(data)
        pages = text.split(MOCK_PAGE_DELIMITER) if MOCK_PAGE_DELIMITER in text else [text]
        return [
            self._page(p, i, document_id, case_id)
            for i, p in enumerate(pages, start=1)
        ]


# --------------------------------------------------------------------------- #
# Local Tesseract provider (real OCR, lazy + graceful)
# --------------------------------------------------------------------------- #
class LocalTesseractOCRProvider(OCRProvider):
    """Offline OCR using pytesseract. Degrades gracefully if unavailable.

    Dependencies (``pytesseract``, ``Pillow``, a Tesseract binary, and PyMuPDF
    for PDF rasterization) are imported lazily inside methods so importing this
    module never fails. ``is_available`` probes for the bindings + binary.
    """

    name = "local-tesseract"
    processing_method = ProcessingMethod.TESSERACT

    def __init__(self, dpi: int = 200) -> None:
        self.dpi = dpi
        self._checked: Optional[bool] = None

    # -- availability -------------------------------------------------- #
    def _probe(self) -> bool:
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            return False
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
        except Exception:  # noqa: BLE001 - binary missing/misconfigured
            return False
        return True

    @property
    def is_available(self) -> bool:
        if self._checked is None:
            self._checked = self._probe()
        return self._checked

    @property
    def capabilities(self) -> set[OCRCapability]:
        return {OCRCapability.IMAGE, OCRCapability.SCANNED_PDF}

    # -- helpers ------------------------------------------------------- #
    def _require(self) -> None:
        if not self.is_available:
            raise OCRNotAvailableError(
                "Tesseract OCR is not available (pytesseract bindings or the "
                "tesseract binary are missing). Install tesseract + "
                "`pip install pytesseract pillow` to enable real OCR."
            )

    def _ocr_pil_image(self, image, page_number, document_id, case_id) -> OCRPageResult:
        import pytesseract

        # Use image_to_data to derive a real confidence from per-word scores.
        text = pytesseract.image_to_string(image)
        confidence = self._mean_confidence(image)
        return OCRPageResult(
            document_id=document_id,
            case_id=case_id,
            page_number=page_number,
            raw_text=text,
            confidence=confidence,
            processing_method=self.processing_method,
        )

    @staticmethod
    def _mean_confidence(image) -> float:
        import pytesseract

        try:
            data = pytesseract.image_to_data(
                image, output_type=pytesseract.Output.DICT
            )
            confs = [int(c) for c in data.get("conf", []) if str(c).lstrip("-").isdigit() and int(c) >= 0]
            if not confs:
                return 0.0
            return round(sum(confs) / len(confs) / 100.0, 4)
        except Exception:  # noqa: BLE001
            return 0.0

    # -- public -------------------------------------------------------- #
    def ocr_image(self, data, *, document_id, case_id=None, filename=None):
        self._require()
        import io
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return [self._ocr_pil_image(img, 1, document_id, case_id)]

    def ocr_pdf(self, data, *, document_id, case_id=None, filename=None):
        self._require()
        import io
        import fitz  # PyMuPDF, already a dependency
        from PIL import Image

        results: list[OCRPageResult] = []
        with fitz.open(stream=data, filetype="pdf") as doc:
            zoom = self.dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            for index, page in enumerate(doc, start=1):
                pix = page.get_pixmap(matrix=matrix)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                results.append(self._ocr_pil_image(img, index, document_id, case_id))
        return results or [
            OCRPageResult(
                document_id=document_id, case_id=case_id, page_number=1,
                raw_text="", confidence=0.0, processing_method=self.processing_method,
            )
        ]


def get_ocr_provider(prefer_real: bool = True) -> OCRProvider:
    """Return the best available OCR provider.

    If ``prefer_real`` and Tesseract is available, returns the Tesseract
    provider; otherwise returns the deterministic mock provider so the workflow
    never breaks when OCR dependencies are absent.
    """
    if prefer_real:
        tess = LocalTesseractOCRProvider()
        if tess.is_available:
            return tess
    return MockOCRProvider()


def describe_ocr_provider(provider: OCRProvider | None = None) -> str:
    """Human-readable description of the active OCR provider."""
    provider = provider or get_ocr_provider()
    if isinstance(provider, LocalTesseractOCRProvider):
        return "Local Tesseract OCR (offline)"
    return "Mock OCR provider (deterministic; Tesseract not installed)"
