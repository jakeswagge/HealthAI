"""Placeholder OCR engine.

A no-op implementation of :class:`OCREngine` that exists so the rest of the
codebase can depend on the OCR interface today. It declares its intended
capabilities but is NOT available: every recognition call raises
:class:`OCRNotAvailableError`.

This is intentional. Real OCR (Tesseract, Textract, Claude Vision, etc.) is out
of scope for this milestone; only the abstraction is being prepared.
"""

from __future__ import annotations

from app.ocr.base import (
    OCRCapability,
    OCREngine,
    OCRNotAvailableError,
    OCRResult,
)


class PlaceholderOCREngine(OCREngine):
    """A non-functional OCR engine used as an architectural placeholder."""

    name = "placeholder"

    @property
    def is_available(self) -> bool:
        return False

    @property
    def capabilities(self) -> set[OCRCapability]:
        # Declares what a future engine is expected to support; does not imply
        # any of it works yet.
        return {OCRCapability.IMAGE, OCRCapability.SCANNED_PDF}

    def _unavailable(self) -> OCRResult:
        raise OCRNotAvailableError(
            "OCR is not implemented yet. The placeholder engine cannot extract "
            "text from images or scanned PDFs. A real OCR engine will be added "
            "in a future milestone."
        )

    def recognize_image(self, data: bytes, *, filename: str | None = None) -> OCRResult:
        return self._unavailable()

    def recognize_pdf(self, data: bytes, *, filename: str | None = None) -> OCRResult:
        return self._unavailable()
