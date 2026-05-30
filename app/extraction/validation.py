"""File validation helpers.

Validation is kept separate from extraction so it can be unit tested in
isolation and reused by the Streamlit UI before any extraction work happens.
"""

from __future__ import annotations

import os

from app.models.document import DocumentType, SUPPORTED_EXTENSIONS


class ValidationError(Exception):
    """Base class for validation problems."""


class UnsupportedFileTypeError(ValidationError):
    """Raised when a file extension is not supported in Milestone 1."""


def _extension(filename: str) -> str:
    """Return the lowercased extension (without the dot) for a filename."""
    if not filename or not filename.strip():
        raise ValidationError("Filename must not be empty.")

    # os.path.splitext handles names with multiple dots correctly,
    # e.g. "report.final.pdf" -> ".pdf".
    _, ext = os.path.splitext(filename)
    return ext.lower().lstrip(".")


def validate_filename(filename: str) -> str:
    """Validate that a filename has a supported extension.

    Args:
        filename: The original uploaded file name.

    Returns:
        The normalized (lowercase, no dot) extension.

    Raises:
        ValidationError: If the filename is empty or has no extension.
        UnsupportedFileTypeError: If the extension is not supported.
    """
    ext = _extension(filename)

    if not ext:
        raise ValidationError(
            f"File '{filename}' has no extension; expected one of: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )

    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '.{ext}'. Supported types: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )

    return ext


def get_document_type(filename: str) -> DocumentType:
    """Validate a filename and return its :class:`DocumentType`.

    Raises:
        ValidationError / UnsupportedFileTypeError: see :func:`validate_filename`.
    """
    ext = validate_filename(filename)
    return SUPPORTED_EXTENSIONS[ext]
