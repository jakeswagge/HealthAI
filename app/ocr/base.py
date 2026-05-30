"""OCR interfaces and result/error types.

Defines the contract a future OCR engine must satisfy. No concrete engine is
implemented in this milestone - this is architecture preparation only.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum


class OCRError(Exception):
    """Base class for OCR-related errors."""


class OCRNotAvailableError(OCRError):
    """Raised when OCR is requested but no engine is available/implemented."""


class OCRCapability(str, Enum):
    """Declared capabilities of an OCR engine."""

    IMAGE = "image"          # OCR of raster images (png/jpg/tiff)
    SCANNED_PDF = "scanned_pdf"  # OCR of image-only / scanned PDFs
    HANDWRITING = "handwriting"  # handwritten text recognition


@dataclass
class OCRResult:
    """Normalized OCR output (shape only; not produced by any real engine yet).

    Attributes:
        text: Recognized text.
        page_count: Number of pages processed.
        engine: Identifier of the engine that produced the result.
        confidence: Optional overall confidence (0.0-1.0) if the engine
            reports one.
        meta: Engine-specific metadata.
    """

    text: str
    page_count: int = 1
    engine: str = "unknown"
    confidence: float | None = None
    meta: dict = field(default_factory=dict)


class OCREngine(abc.ABC):
    """Interface every OCR engine must implement.

    Implementations are expected to be added in a future milestone. They should
    raise :class:`OCRNotAvailableError` from :meth:`recognize_*` until they are
    fully functional, and report ``is_available = False`` so callers can detect
    that OCR is not yet usable.
    """

    #: Human-readable engine name.
    name: str = "abstract"

    @property
    @abc.abstractmethod
    def is_available(self) -> bool:
        """True if this engine can actually perform OCR right now."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def capabilities(self) -> set[OCRCapability]:
        """The set of capabilities this engine declares."""
        raise NotImplementedError

    @abc.abstractmethod
    def recognize_image(self, data: bytes, *, filename: str | None = None) -> OCRResult:
        """Recognize text in a raster image's bytes.

        Raises:
            OCRNotAvailableError: if the engine is a placeholder/unavailable.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def recognize_pdf(self, data: bytes, *, filename: str | None = None) -> OCRResult:
        """Recognize text in a scanned/image-only PDF's bytes.

        Raises:
            OCRNotAvailableError: if the engine is a placeholder/unavailable.
        """
        raise NotImplementedError
