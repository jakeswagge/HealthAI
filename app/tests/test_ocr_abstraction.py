"""Tests for the OCR abstraction layer (interfaces + placeholder only)."""

from __future__ import annotations

import pytest

from app.ocr import (
    OCRCapability,
    OCREngine,
    OCRNotAvailableError,
    OCRResult,
    PlaceholderOCREngine,
    get_ocr_engine,
    is_ocr_available,
)


class TestPlaceholderEngine:
    def test_implements_interface(self):
        engine = PlaceholderOCREngine()
        assert isinstance(engine, OCREngine)
        assert engine.name == "placeholder"

    def test_not_available(self):
        assert PlaceholderOCREngine().is_available is False

    def test_declares_capabilities(self):
        caps = PlaceholderOCREngine().capabilities
        assert OCRCapability.IMAGE in caps
        assert OCRCapability.SCANNED_PDF in caps

    def test_recognize_image_raises_not_available(self):
        with pytest.raises(OCRNotAvailableError):
            PlaceholderOCREngine().recognize_image(b"\x89PNG...")

    def test_recognize_pdf_raises_not_available(self):
        with pytest.raises(OCRNotAvailableError):
            PlaceholderOCREngine().recognize_pdf(b"%PDF-1.7...")


class TestFactory:
    def test_returns_placeholder(self):
        engine = get_ocr_engine()
        assert isinstance(engine, PlaceholderOCREngine)

    def test_is_ocr_available_false(self):
        assert is_ocr_available() is False


class TestOCRResultShape:
    def test_defaults(self):
        result = OCRResult(text="abc")
        assert result.text == "abc"
        assert result.page_count == 1
        assert result.engine == "unknown"
        assert result.confidence is None
        assert result.meta == {}
