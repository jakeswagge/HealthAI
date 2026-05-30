"""Document text extraction package."""

from app.extraction.validation import (
    UnsupportedFileTypeError,
    ValidationError,
    get_document_type,
    validate_filename,
)
from app.extraction.extractor import (
    extract_text,
    extract_text_from_bytes,
    extract_txt,
    extract_pdf,
    extract_pages_from_bytes,
    join_pages,
    PAGE_DELIMITER,
)
from app.extraction.size_validator import (
    DocumentSizeReport,
    DocumentSizeValidator,
    SizeSeverity,
    SizeThresholds,
    estimate_tokens,
)

__all__ = [
    "UnsupportedFileTypeError",
    "ValidationError",
    "get_document_type",
    "validate_filename",
    "extract_text",
    "extract_text_from_bytes",
    "extract_txt",
    "extract_pdf",
    "extract_pages_from_bytes",
    "join_pages",
    "PAGE_DELIMITER",
    "DocumentSizeReport",
    "DocumentSizeValidator",
    "SizeSeverity",
    "SizeThresholds",
    "estimate_tokens",
]
