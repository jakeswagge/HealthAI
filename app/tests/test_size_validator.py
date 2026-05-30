"""Tests for DocumentSizeValidator (large-document detect-and-warn)."""

from __future__ import annotations

from app.extraction.size_validator import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_PAGES,
    DEFAULT_MAX_TOKENS,
    DocumentSizeValidator,
    SizeSeverity,
    SizeThresholds,
    estimate_tokens,
)


class TestEstimateTokens:
    def test_zero(self):
        assert estimate_tokens(0) == 0

    def test_ceiling_division(self):
        assert estimate_tokens(4) == 1
        assert estimate_tokens(5) == 2  # ceil(5/4)
        assert estimate_tokens(8) == 2

    def test_custom_divisor(self):
        assert estimate_tokens(10, chars_per_token=5) == 2


class TestWithinThresholds:
    def test_small_document_is_ok(self):
        validator = DocumentSizeValidator()
        report = validator.assess("hello world", page_count=1)
        assert report.severity is SizeSeverity.OK
        assert report.exceeds_threshold is False
        assert report.warnings == []
        assert report.char_count == len("hello world")
        assert report.estimated_tokens == estimate_tokens(len("hello world"))


class TestExceedsThresholds:
    def test_too_many_chars_warns(self):
        validator = DocumentSizeValidator()
        text = "x" * (DEFAULT_MAX_CHARS + 1)
        report = validator.assess(text, page_count=1)
        assert report.severity is SizeSeverity.WARNING
        assert any("characters" in w for w in report.warnings)

    def test_too_many_pages_warns(self):
        validator = DocumentSizeValidator()
        report = validator.assess("short", page_count=DEFAULT_MAX_PAGES + 5)
        assert report.exceeds_threshold
        assert any("pages" in w for w in report.warnings)

    def test_too_many_tokens_warns(self):
        validator = DocumentSizeValidator()
        # Exceed token threshold but stay under char threshold is impossible
        # with 4 chars/token if char threshold is larger; use a custom config.
        validator = DocumentSizeValidator(
            SizeThresholds(max_pages=999, max_chars=10**9, max_tokens=10)
        )
        report = validator.assess("y" * 100, page_count=1)
        assert report.exceeds_threshold
        assert any("token" in w for w in report.warnings)

    def test_multiple_warnings(self):
        validator = DocumentSizeValidator(
            SizeThresholds(max_pages=1, max_chars=10, max_tokens=2)
        )
        report = validator.assess("z" * 100, page_count=10)
        assert len(report.warnings) == 3


class TestCustomThresholdsAndSerialization:
    def test_custom_thresholds_respected(self):
        validator = DocumentSizeValidator(SizeThresholds(max_chars=5))
        report = validator.assess("123456", page_count=1)
        assert report.exceeds_threshold

    def test_as_dict_roundtrip(self):
        validator = DocumentSizeValidator()
        d = validator.assess("hello", 1).as_dict()
        assert set(d) >= {
            "page_count",
            "char_count",
            "estimated_tokens",
            "severity",
            "warnings",
            "thresholds",
        }
        assert d["thresholds"]["max_tokens"] == DEFAULT_MAX_TOKENS

    def test_assess_document_like_object(self):
        class Doc:
            text = "hello world"
            page_count = 2

        report = DocumentSizeValidator().assess_document(Doc())
        assert report.page_count == 2
        assert report.char_count == len("hello world")
