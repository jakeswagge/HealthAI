"""Intelligent document ingestion.

The :class:`DocumentIngestionEngine` detects a file's type, decides whether it
already has a usable text layer or needs OCR, runs the appropriate path, and
returns page-level text plus OCR metadata. It is the single entry point that
turns any supported upload (TXT, searchable PDF, scanned PDF, PNG/JPG/JPEG)
into page text the evidence/assembly layers already understand.

Independent of review/appeal/audit: it only produces text + OCR results.
"""

from app.ingestion.engine import (
    DocumentIngestionEngine,
    IngestionResult,
    IngestionKind,
)
from app.ingestion.classifier import DocumentClassifier

__all__ = [
    "DocumentIngestionEngine",
    "IngestionResult",
    "IngestionKind",
    "DocumentClassifier",
]
