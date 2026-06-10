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
from app.models.review_result import (
    CriterionEvaluation,
    CriterionStatus,
    Recommendation,
    ReviewResult,
)
from app.review.engine import ClinicalReviewEngine
from app.review.review_prompts import (
    REVIEW_SYSTEM_PROMPT,
    build_review_messages,
    build_review_selection_messages,
)
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
        max_tokens: int = 3000,
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
        backend = self.backend_name
        model = getattr(self.llm, "model", backend)
        retrieved_guidelines = self.repository.retrieve(case)

        # No applicable guideline: defer to the deterministic engine's
        # well-formed result unless a real AI backend can select from the local
        # guideline library.
        if match is None:
            if getattr(self.llm, "is_ai", False):
                guidelines = self.repository.all()
                if guidelines:
                    messages = build_review_selection_messages(
                        case, guidelines, document_text
                    )
                    ai_result = self._run_ai_review(
                        messages=messages,
                        guideline=None,
                        retrieved_guidelines=retrieved_guidelines,
                    )
                    if ai_result is not None:
                        return ai_result

            result = self.engine.review(case, document_text)
            self._stamp_review_metadata(
                result,
                used_ai=False,
                backend=backend,
                model=model,
            )
            result.evidence_refs["retrieved_guidelines"] = [
                item["guideline_id"] for item in retrieved_guidelines
            ]
            return ReviewAgentResult(
                result=result,
                attempts=0,
                backend=backend,
                model=model,
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
            self._stamp_review_metadata(
                result,
                used_ai=False,
                backend=backend,
                model=self.backend_name,
            )
            result.evidence_refs["retrieved_guidelines"] = [
                item["guideline_id"] for item in retrieved_guidelines
            ]
            return ReviewAgentResult(
                result=result,
                attempts=0,
                backend=self.backend_name,
                model=self.backend_name,
                used_ai=False,
                guideline_id=guideline.guideline_id,
            )

        messages = build_review_messages(case, guideline, document_text)
        ai_result = self._run_ai_review(
            messages=messages,
            guideline=guideline,
            retrieved_guidelines=retrieved_guidelines,
        )
        if ai_result is not None:
            return ai_result

        # Exhausted retries or backend error on a real AI backend: fall back
        # deterministically rather than failing the user request.
        errors = getattr(self, "_last_ai_errors", [])
        last_raw = getattr(self, "_last_ai_raw_text", "")
        result = self.engine.review(case, document_text)
        result.guideline_id = guideline.guideline_id
        result.service_name = guideline.service_name
        self._stamp_review_metadata(
            result,
            used_ai=False,
            backend=backend,
            model=backend,
        )
        result.evidence_refs["retrieved_guidelines"] = [
            item["guideline_id"] for item in retrieved_guidelines
        ]
        return ReviewAgentResult(
            result=result,
            attempts=self.max_retries,
            backend=backend,
            model=backend,
            used_ai=False,
            repaired=True,
            guideline_id=guideline.guideline_id,
            raw_text=last_raw,
            errors=errors,
        )

    def _run_ai_review(
        self,
        *,
        messages: list[dict[str, str]],
        guideline,
        retrieved_guidelines: list[dict] | None = None,
    ) -> ReviewAgentResult | None:
        """Prompt the AI backend and return a valid review, or None on fallback."""
        errors: list[str] = []
        last_raw = ""
        guideline_id = guideline.guideline_id if guideline is not None else None
        service_name = guideline.service_name if guideline is not None else None

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
                if guideline is not None:
                    result.guideline_id = guideline.guideline_id
                    result.service_name = guideline.service_name
                elif result.guideline_id:
                    matched = self.repository.get(result.guideline_id)
                    if matched is None:
                        result.guideline_id = None
                        result.service_name = None
                        result.recommendation = Recommendation.INSUFFICIENT_INFORMATION
                        result.matched_criteria = []
                        result.missing_criteria = []
                        result.missing_evidence = [
                            "No matching local clinical guideline was available."
                        ]
                    else:
                        result.guideline_id = matched.guideline_id
                        result.service_name = matched.service_name
                    guideline_id = result.guideline_id
                    service_name = result.service_name
                else:
                    result.guideline_id = None
                    result.service_name = None
                    result.recommendation = Recommendation.INSUFFICIENT_INFORMATION
                    result.matched_criteria = []
                    result.missing_criteria = []
                    result.missing_evidence = [
                        "No matching local clinical guideline was available."
                    ]
                if result.confidence_score <= 0.0:
                    result.confidence_score = 0.6
                self._stamp_review_metadata(
                    result,
                    used_ai=True,
                    backend=self.backend_name,
                    model=response.model,
                )
                if retrieved_guidelines:
                    result.evidence_refs["retrieved_guidelines"] = [
                        item["guideline_id"] for item in retrieved_guidelines
                    ]
                if service_name and not result.service_name:
                    result.service_name = service_name
                self._ensure_criteria_detail(
                    result,
                    guideline=guideline,
                    backend=self.backend_name,
                )

                return ReviewAgentResult(
                    result=result,
                    attempts=attempt,
                    backend=self.backend_name,
                    model=response.model,
                    used_ai=True,
                    repaired=attempt > 1,
                    guideline_id=guideline_id,
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
                break

        self._last_ai_errors = errors
        self._last_ai_raw_text = last_raw
        return None

    @staticmethod
    def _stamp_review_metadata(
        result: ReviewResult,
        *,
        used_ai: bool,
        backend: str,
        model: str,
    ) -> None:
        """Persist backend provenance on the review artifact itself."""
        result.generated_by_ai = used_ai
        result.review_backend = backend
        result.review_model = model
        result.reasoning_backend = backend if used_ai else None
        result.reasoning_model = model if used_ai else None
        for detail in result.criteria_detail:
            if not detail.review_backend:
                detail.review_backend = backend

    @staticmethod
    def _ensure_criteria_detail(
        result: ReviewResult,
        *,
        guideline,
        backend: str,
    ) -> None:
        """Ensure AI/local review has one rule-level row per criterion."""
        if guideline is None:
            for detail in result.criteria_detail:
                if not detail.review_backend:
                    detail.review_backend = backend
                if not detail.reasoning and detail.note:
                    detail.reasoning = detail.note
            return

        by_id = {detail.id: detail for detail in result.criteria_detail}
        matched = {_norm_text(item) for item in result.matched_criteria}
        missing = {_norm_text(item) for item in result.missing_criteria}

        ordered: list[CriterionEvaluation] = []
        for criterion in guideline.required_criteria:
            detail = by_id.get(criterion.id)
            description_key = _norm_text(criterion.description)
            if detail is None:
                if description_key in matched:
                    status = CriterionStatus.MET
                    note = "Criterion was listed as satisfied by the reviewer."
                    missing_evidence: list[str] = []
                elif description_key in missing:
                    status = CriterionStatus.NOT_MET
                    note = "Criterion was listed as missing by the reviewer."
                    missing_evidence = [f"Evidence for: {criterion.description}"]
                else:
                    status = CriterionStatus.UNKNOWN
                    note = "Criterion was not explicitly evaluated by the reviewer."
                    missing_evidence = [
                        f"Documentation needed to establish: {criterion.description}"
                    ]
                detail = CriterionEvaluation(
                    id=criterion.id,
                    description=criterion.description,
                    met=status is CriterionStatus.MET,
                    status=status,
                    note=note,
                    reasoning=note,
                    missing_evidence=missing_evidence,
                    confidence_score=0.85 if status is CriterionStatus.MET else 0.55,
                    review_backend=backend,
                )
            else:
                if not detail.description:
                    detail.description = criterion.description
                if not detail.review_backend:
                    detail.review_backend = backend
                if not detail.reasoning and detail.note:
                    detail.reasoning = detail.note
                if (
                    detail.status is not CriterionStatus.MET
                    and not detail.missing_evidence
                ):
                    detail.missing_evidence = [f"Evidence for: {criterion.description}"]
            ordered.append(detail)

        extra = [
            detail
            for detail in result.criteria_detail
            if detail.id not in {criterion.id for criterion in guideline.required_criteria}
        ]
        for detail in extra:
            if not detail.review_backend:
                detail.review_backend = backend
        result.criteria_detail = [*ordered, *extra]


def _norm_text(value: str) -> str:
    return " ".join(str(value).lower().split())
