"""Tests for file validation logic."""

from __future__ import annotations

import pytest

from app.extraction.validation import (
    UnsupportedFileTypeError,
    ValidationError,
    get_document_type,
    validate_filename,
)
from app.models.document import DocumentType


class TestValidateFilename:
    def test_accepts_txt(self):
        assert validate_filename("report.txt") == "txt"

    def test_accepts_pdf(self):
        assert validate_filename("scan.pdf") == "pdf"

    def test_is_case_insensitive(self):
        assert validate_filename("REPORT.PDF") == "pdf"
        assert validate_filename("Notes.Txt") == "txt"

    def test_handles_multiple_dots(self):
        assert validate_filename("denial.case.01.pdf") == "pdf"

    def test_rejects_unsupported_extension(self):
        with pytest.raises(UnsupportedFileTypeError):
            validate_filename("image.png")

    def test_rejects_docx(self):
        with pytest.raises(UnsupportedFileTypeError):
            validate_filename("appeal.docx")

    def test_rejects_no_extension(self):
        with pytest.raises(ValidationError):
            validate_filename("README")

    def test_rejects_empty_filename(self):
        with pytest.raises(ValidationError):
            validate_filename("")

    def test_rejects_whitespace_filename(self):
        with pytest.raises(ValidationError):
            validate_filename("   ")


class TestGetDocumentType:
    def test_txt_maps_to_txt_type(self):
        assert get_document_type("a.txt") is DocumentType.TXT

    def test_pdf_maps_to_pdf_type(self):
        assert get_document_type("a.pdf") is DocumentType.PDF

    def test_unsupported_raises(self):
        with pytest.raises(UnsupportedFileTypeError):
            get_document_type("a.gif")
