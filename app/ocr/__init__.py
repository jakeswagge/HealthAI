"""OCR abstraction layer (interfaces + placeholder only).

This package prepares the architecture for optical character recognition of
scanned / image-only PDFs. It defines the *interface* a future OCR engine must
implement and ships a no-op placeholder. There is intentionally NO engine
integration here:

- No Tesseract
- No AWS Textract
- No Claude Vision
- No image processing

When a real engine is added later, it implements :class:`OCREngine` and is
selected by :func:`get_ocr_engine`; nothing else in the codebase needs to
change. This mirrors how the LLM service layer isolates Claude.
"""

from app.ocr.base import (
    OCRCapability,
    OCREngine,
    OCRError,
    OCRNotAvailableError,
    OCRResult,
)
from app.ocr.placeholder import PlaceholderOCREngine
from app.ocr.factory import get_ocr_engine, is_ocr_available
from app.ocr.providers import (
    LocalTesseractOCRProvider,
    MockOCRProvider,
    OCRReadiness,
    OCRProvider,
    describe_ocr_provider,
    get_ocr_provider,
    ocr_readiness,
)

__all__ = [
    "OCRCapability",
    "OCREngine",
    "OCRError",
    "OCRNotAvailableError",
    "OCRResult",
    "PlaceholderOCREngine",
    "get_ocr_engine",
    "is_ocr_available",
    "OCRProvider",
    "OCRReadiness",
    "LocalTesseractOCRProvider",
    "MockOCRProvider",
    "get_ocr_provider",
    "describe_ocr_provider",
    "ocr_readiness",
]
