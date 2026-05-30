"""Tests for PDF extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.extraction.extractor import (
    extract_pdf,
    extract_text,
    extract_text_from_bytes,
)
from app.models.document import DocumentType


class TestExtractPdf:
    def test_extracts_known_text(self, pdf_bytes: bytes):
        text, pages = extract_pdf(pdf_bytes)
        assert "HealthAI prior authorization sample PDF." in text
        assert pages == 1

    def test_multi_page_count(self):
        import fitz

        doc = fitz.open()
        for i in range(3):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {i + 1} content")
        data = doc.tobytes()
        doc.close()

        text, pages = extract_pdf(data)
        assert pages == 3
        assert "Page 1 content" in text
        assert "Page 3 content" in text


class TestExtractPdfFromBytes:
    def test_returns_extracted_document(self, pdf_bytes: bytes):
        doc = extract_text_from_bytes("sample.pdf", pdf_bytes)
        assert doc.document_type is DocumentType.PDF
        assert doc.filename == "sample.pdf"
        assert doc.page_count == 1
        assert "Line two of the PDF." in doc.text
        assert doc.is_empty is False


class TestExtractPdfFromDisk:
    def test_reads_temp_pdf_file(self, tmp_pdf_file: Path):
        doc = extract_text(tmp_pdf_file)
        assert doc.document_type is DocumentType.PDF
        assert "HealthAI prior authorization sample PDF." in doc.text


class TestExtractErrors:
    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            extract_text("does_not_exist.pdf")

    def test_corrupt_pdf_raises(self):
        with pytest.raises(Exception):
            extract_pdf(b"this is not a real pdf")
