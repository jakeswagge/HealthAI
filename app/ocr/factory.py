"""OCR engine selection.

Currently only the :class:`PlaceholderOCREngine` exists, so the factory always
returns it. When a real engine is added in a future milestone, this factory is
the single place that needs to change (mirroring the LLM service factory).
"""

from __future__ import annotations

from app.ocr.base import OCREngine
from app.ocr.placeholder import PlaceholderOCREngine


def get_ocr_engine() -> OCREngine:
    """Return the active OCR engine.

    For now this is always the placeholder (OCR is not implemented).
    """
    return PlaceholderOCREngine()


def is_ocr_available() -> bool:
    """True if a functional OCR engine is currently available."""
    return get_ocr_engine().is_available
