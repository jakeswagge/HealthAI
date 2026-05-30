"""Tests for TXT extraction."""

from __future__ import annotations

from pathlib import Path

from app.extraction.extractor import (
    extract_text,
    extract_text_from_bytes,
    extract_txt,
)
from app.models.document import DocumentType


class TestExtractTxt:
    def test_decodes_utf8(self, txt_bytes: bytes):
        text, pages = extract_txt(txt_bytes)
        assert "Prior authorization denial notice." in text
        assert pages == 1

    def test_falls_back_to_latin1(self):
        # 0xff is invalid as a standalone UTF-8 byte; latin-1 maps it to 'ÿ'.
        text, pages = extract_txt(b"caf\xe9 \xff")
        assert "caf" in text
        assert pages == 1

    def test_empty_bytes(self):
        text, pages = extract_txt(b"")
        assert text == ""
        assert pages == 1


class TestExtractTxtFromBytes:
    def test_returns_extracted_document(self, txt_bytes: bytes):
        doc = extract_text_from_bytes("denial.txt", txt_bytes)
        assert doc.document_type is DocumentType.TXT
        assert doc.filename == "denial.txt"
        assert doc.page_count == 1
        assert "Member: Test Patient" in doc.text
        assert doc.char_count == len(doc.text)
        assert doc.word_count > 0
        assert doc.is_empty is False


class TestExtractTxtFromDisk:
    def test_reads_temp_file(self, tmp_txt_file: Path):
        doc = extract_text(tmp_txt_file)
        assert doc.document_type is DocumentType.TXT
        assert "CPT 73721" in doc.text

    def test_reads_sample_denial_file(self, sample_txt_file: Path):
        doc = extract_text(sample_txt_file)
        assert doc.document_type is DocumentType.TXT
        assert "DENIED" in doc.text
        assert "Cardiac MRI with contrast" in doc.text
