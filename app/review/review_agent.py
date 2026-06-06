"""GuidelineReviewAgent: Claude-backed clinical guideline review.

Responsibilities:
- Read a :class:`PatientCase`.
- Find the applicable :class:`ClinicalGuideline` (via the guideline repo).
- Compare the case evidence to the guideline criteria.
- Generate a structured, validated :class:`ReviewResult` explanation.
- Return structured JSON, validated with pydantic, retrying on invalid output.

Backend behavior
----------------
All model access goes through the service-layer :class:`LLMClient`, keeping AI
isolated. When the active backend is a real AI model (Claude), the agent
prompts the model and validates/retries its JSON. When no AI backend is
configured (the offline local heuristic backend), the agent transparently
falls back to the deterministic :class:`ClinicalReviewEngine`, so the feature
is always usable locally and the JSON contract is identical either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError as PydanticValidationError

from app.guidelines.repository import (
    GuidelineRepository,
    get_default_repository,
)
from app.models.patient_case import PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.review.engine import ClinicalReviewEngine
from app.review.review_prompts import REVIEW_SYSTEM_PROMPT, build_review_messages
from app.services.factory import get_llm_client
from app.services.json_utils import extract_json_object as _extract_json_object
from app.services.llm_client import LLMClient, LLMError


class ReviewAgentError(Exception):
    """Raised when the review agent cannot produce a valid result."""


@dataclass
class ReviewAgentResult:
    """Outcome of a review-agent run."""

    result: ReviewResult
    attempts: int
    backend: str
    model: str
    used_ai: bool
    repaired: bool = False
    guideline_id: str | None = None
    raw_text: str = ""
    errors: list[str] = field(default_factory=list)


# ``_extract_json_object`` is the shared helper from app.services.json_utils
# (Milestone 12 de-duplication); behavior unchanged.


class GuidelineReviewAgent:
    """Reviews a PatientCase against the applicable clinical guideline."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        repository: GuidelineRepository | None = None,
        max_retries: int = 3,
        max_tokens: int = 1200,
    ) -> None:
        self.llm = llm_client or get_llm_client()
        self.repository = repository or get_default_repository()
        self.engine = ClinicalReviewEngine(repository=self.repository)
        self.max_retries = max(1, max_retries)
        self.max_tokens = max_tokens

    @property
    def backend_name(self) -> str:
        return getattr(self.llm, "name", "unknown")

    def review(
        self,
        case: PatientCase,
        document_text: str | None = None,
    ) -> ReviewAgentResult:
        """Produce a validated ReviewResult for a case."""
        match = self.repository.match(case)

        # No applicable guideline: defer to the deterministic engine's
        # well-formed "insufficient information" result.
        if match is None:
            result = self.engine.review(case, document_text)
            return ReviewAgentResult(
                result=result,
                attempts=0,
                backend=self.backend_name,
                model=getattr(self.llm, "model", self.backend_name),
                used_ai=False,
                guideline_id=None,
            )

        guideline = match.guideline

        # Offline / non-AI backend: use the deterministic engine. Same JSON
        # contract, fully validated.
        if not getattr(self.llm, "is_ai", False):
            result = self.engine.review(case, document_text)
            result.guideline_id = guideline.guideline_id
            result.service_name = guideline.service_name
            return ReviewAgentResult(
                result=result,
                attempts=0,
                backend=self.backend_name,
                model=self.backend_name,
                used_ai=False,
                guideline_id=guideline.guideline_id,
            )

        # AI backend: prompt + validate + retry.
        messages = build_review_messages(case, guideline, document_text)
        errors: list[str] = []
        last_raw = ""

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.llm.complete(
                    system=REVIEW_SYSTEM_PROMPT,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=0.0,
                )
                last_raw = response.text
                data = _extract_json_object(response.text)
                result = ReviewResult.model_validate(data)

                # Stamp guideline identity + sane confidence fallback.
                result.guideline_id = guideline.guideline_id
                result.service_name = guideline.service_name
                if result.confidence_score <= 0.0:
                    result.confidence_score = 0.6

                return ReviewAgentResult(
                    result=result,
                    attempts=attempt,
                    backend=self.backend_name,
                    model=response.model,
                    used_ai=True,
                    repaired=attempt > 1,
                    guideline_id=guideline.guideline_id,
                    raw_text=last_raw,
                    errors=errors,
                )

            except (ValueError, PydanticValidationError) as exc:
                errors.append(f"Attempt {attempt}: {type(exc).__name__}: {exc}")
                if attempt < self.max_retries:
                    messages = messages + [
                        {
                            "role": "user",
                            "content": (
                                "Your previous response was not valid against "
                                f"the required schema. Error: {exc}. Respond "
                                "again with VALID JSON ONLY containing exactly "
                                "the required keys. No commentary."
                            ),
                        }
                    ]
                    continue

            except LLMError as exc:
                errors.append(f"Attempt {attempt}: LLMError: {exc}")
                # Backend failure: degrade gracefully to deterministic engine.
                result = self.engine.review(case, document_text)
                result.guideline_id = guideline.guideline_id
                result.service_name = guideline.service_name
                return ReviewAgentResult(
                    result=result,
                    attempts=attempt,
                    backend=self.backend_name,
                    model=self.backend_name,
                    used_ai=False,
                    guideline_id=guideline.guideline_id,
                    errors=errors,
                )

        # Exhausted retries on a real AI backend: fall back deterministically
        # rather than failing the user request.
        result = self.engine.review(case, document_text)
        result.guideline_id = guideline.guideline_id
        result.service_name = guideline.service_name
        return ReviewAgentResult(
            result=result,
            attempts=self.max_retries,
            backend=self.backend_name,
            model=self.backend_name,
            used_ai=False,
            repaired=True,
            guideline_id=guideline.guideline_id,
            raw_text=last_raw,
            errors=errors,
        )
