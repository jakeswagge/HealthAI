"""AI agents for HealthAI.

Agents orchestrate prompting + validation + retry. All raw model access is
delegated to the service layer (``app.services``) so the AI dependency stays
isolated and swappable.
"""

from app.agents.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_messages,
    build_user_prompt,
)
from app.agents.medical_extraction_agent import (
    ExtractionError,
    ExtractionResult,
    MedicalExtractionAgent,
)

__all__ = [
    "EXTRACTION_SYSTEM_PROMPT",
    "build_extraction_messages",
    "build_user_prompt",
    "ExtractionError",
    "ExtractionResult",
    "MedicalExtractionAgent",
]
