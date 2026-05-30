"""Prior-authorization appeal generation.

This package is independent of the extraction, review, and OCR engines. It
takes the structured artifacts those engines produce - a :class:`PatientCase`,
a :class:`ReviewResult`, and (optionally) the matched :class:`ClinicalGuideline`
- and generates a professional :class:`AppealLetter`.

Two entry points:
- :class:`AppealLetterBuilder`: deterministic, offline letter assembly used as
  the local default and as the fallback when no AI backend is available.
- :class:`AppealGenerationAgent`: Claude-backed generation via the service
  layer, with structured-JSON-first output, pydantic validation, and retry.
"""

from app.appeals.builder import AppealLetterBuilder, render_letter_text
from app.appeals.appeal_agent import (
    AppealGenerationAgent,
    AppealAgentError,
    AppealAgentResult,
)

__all__ = [
    "AppealLetterBuilder",
    "render_letter_text",
    "AppealGenerationAgent",
    "AppealAgentError",
    "AppealAgentResult",
]
