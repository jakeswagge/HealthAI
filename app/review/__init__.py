"""Clinical review engine.

This package is independent of the extraction engine. It takes a structured
:class:`PatientCase`, finds the applicable :class:`ClinicalGuideline`, and
produces a validated :class:`ReviewResult` explaining the recommendation.

Two entry points:
- :class:`ClinicalReviewEngine`: deterministic, offline, rule-based review.
- :class:`GuidelineReviewAgent`: Claude-backed review via the service layer,
  with JSON validation and retry (falls back to the deterministic engine
  result shape when no AI backend is configured).
"""

from app.review.engine import ClinicalReviewEngine
from app.review.review_agent import (
    GuidelineReviewAgent,
    ReviewAgentError,
    ReviewAgentResult,
)

__all__ = [
    "ClinicalReviewEngine",
    "GuidelineReviewAgent",
    "ReviewAgentError",
    "ReviewAgentResult",
]
