"""Pydantic model for a single page's OCR result (Milestone 9).

This is the spec's page-level "OCRResult". It is named ``OCRPageResult`` to
avoid colliding with the existing aggregate ``app.ocr.base.OCRResult``
dataclass (which remains for backward compatibility). Every OCR-derived fact
can be traced back to one of these via ``document_id`` + ``page_number``, and
each carries the ``processing_method`` and OCR ``confidence`` so reviewers can
judge reliability.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_ocr_id() -> str:
    return f"OCR-{uuid.uuid4().hex[:12].upper()}"


class ProcessingMethod(str, Enum):
    """How a page's text was produced."""

    TESSERACT = "TESSERACT"          # real local OCR (pytesseract)
    VISION_MODEL = "VISION_MODEL"    # future vision LLM (not implemented)
    PLACEHOLDER = "PLACEHOLDER"      # no-op placeholder engine
    MOCK = "MOCK"                    # deterministic offline test provider
    TEXT_LAYER = "TEXT_LAYER"        # extracted from an existing text layer


# Default OCR confidence threshold below which a result is flagged for review.
DEFAULT_OCR_CONFIDENCE_THRESHOLD = 0.60


class OCRPageResult(BaseModel):
    """The OCR (or text-layer) result for a single document page."""

    ocr_id: str = Field(default_factory=new_ocr_id)
    document_id: str = Field(..., description="Owning CaseDocument id.")
    case_id: Optional[str] = Field(default=None)
    page_number: int = Field(default=1, ge=1, description="1-indexed page.")
    raw_text: str = Field(default="", description="Recognized text for the page.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    processing_method: ProcessingMethod = Field(default=ProcessingMethod.MOCK)
    timestamp: str = Field(default_factory=_utc_now_iso)

    @field_validator("processing_method", mode="before")
    @classmethod
    def _coerce_method(cls, v):
        if isinstance(v, ProcessingMethod):
            return v
        if v is None:
            return ProcessingMethod.MOCK
        return ProcessingMethod(str(v).strip().upper())

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_conf(cls, v):
        if v is None:
            return 0.0
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    @field_validator("raw_text", mode="before")
    @classmethod
    def _coerce_text(cls, v):
        return "" if v is None else str(v)

    def is_low_confidence(
        self, threshold: float = DEFAULT_OCR_CONFIDENCE_THRESHOLD
    ) -> bool:
        """True if this page's OCR confidence is below ``threshold``."""
        return self.confidence < threshold

    @computed_field  # type: ignore[prop-decorator]
    @property
    def char_count(self) -> int:
        return len(self.raw_text)
