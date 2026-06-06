"""AppealGenerationAgent: Claude-backed appeal-letter generation.

Inputs:  PatientCase, ReviewResult, optional ClinicalGuideline
Output:  AppealLetter (validated)

LLM behavior (Claude Opus via the service layer):
- Professional healthcare tone, insurance-appeal format.
- Structured JSON first, generated letter second (inside ``letter_text``).
- No hallucinated clinical facts; cite only available evidence; identify
  missing evidence.
- Validate with pydantic; retry on invalid output.

Backend behavior mirrors the review agent: when no AI backend is configured
(offline), or the AI backend fails/exhausts retries, the agent transparently
falls back to the deterministic :class:`AppealLetterBuilder`. The AppealLetter
contract is identical in both modes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import ValidationError as PydanticValidationError

from app.appeals.appeal_prompts import APPEAL_SYSTEM_PROMPT, build_appeal_messages
from app.appeals.builder import (
    AppealLetterBuilder,
    new_appeal_id,
    render_letter_text,
)
from app.guidelines.repository import GuidelineRepository, get_default_repository
from app.models.appeal_letter import AppealLetter
from app.models.clinical_guideline import ClinicalGuideline
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.services.factory import get_llm_client
from app.services.json_utils import extract_json_object as _extract_json_object
from app.services.llm_client import LLMClient, LLMError


class AppealAgentError(Exception):
    """Raised when appeal generation fails unrecoverably."""


@dataclass
class AppealAgentResult:
    """Outcome of an appeal-generation run."""

    appeal: AppealLetter
    attempts: int
    backend: str
    model: str
    used_ai: bool
    repaired: bool = False
    raw_text: str = ""
    errors: list[str] = field(default_factory=list)


# ``_extract_json_object`` is the shared helper from app.services.json_utils
# (Milestone 12 de-duplication); behavior unchanged.


class AppealGenerationAgent:
    """Generates a validated :class:`AppealLetter` from review artifacts."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        repository: GuidelineRepository | None = None,
        max_retries: int = 3,
        max_tokens: int = 2000,
    ) -> None:
        self.llm = llm_client or get_llm_client()
        self.repository = repository or get_default_repository()
        self.builder = AppealLetterBuilder()
        self.max_retries = max(1, max_retries)
        self.max_tokens = max_tokens

    @property
    def backend_name(self) -> str:
        return getattr(self.llm, "name", "unknown")

    def _resolve_guideline(
        self,
        case: PatientCase,
        guideline: ClinicalGuideline | None,
        review: ReviewResult,
    ) -> ClinicalGuideline | None:
        """Find the guideline to cite (explicit > review id > matched)."""
        if guideline is not None:
            return guideline
        if review.guideline_id:
            found = self.repository.get(review.guideline_id)
            if found is not None:
                return found
        match = self.repository.match(case)
        return match.guideline if match else None

    def generate(
        self,
        case: PatientCase,
        review: ReviewResult,
        guideline: ClinicalGuideline | None = None,
    ) -> AppealAgentResult:
        """Generate an appeal letter from the structured inputs."""
        resolved_guideline = self._resolve_guideline(case, guideline, review)

        if case.decision is not Decision.DENIED:
            raise AppealAgentError(
                "Appeal blocked: No active insurance denial found for this case file."
            )

        if (
            review.recommendation.value == "INSUFFICIENT_INFORMATION"
            or not case.requested_service
        ):
            appeal = self.builder.build(case, review, resolved_guideline)
            return AppealAgentResult(
                appeal=appeal,
                attempts=0,
                backend=self.backend_name,
                model=self.backend_name,
                used_ai=False,
            )

        if (
            review.recommendation is Recommendation.DENY
            and review.missing_criteria
        ):
            appeal = self.builder.build(case, review, resolved_guideline)
            return AppealAgentResult(
                appeal=appeal,
                attempts=0,
                backend=self.backend_name,
                model=self.backend_name,
                used_ai=False,
            )

        # Offline / non-AI backend: deterministic builder.
        if not getattr(self.llm, "is_ai", False):
            appeal = self.builder.build(case, review, resolved_guideline)
            return AppealAgentResult(
                appeal=appeal,
                attempts=0,
                backend=self.backend_name,
                model=self.backend_name,
                used_ai=False,
            )

        # AI backend: prompt + validate + retry.
        messages = build_appeal_messages(case, review, resolved_guideline)
        errors: list[str] = []
        last_raw = ""

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.llm.complete(
                    system=APPEAL_SYSTEM_PROMPT,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=0.0,
                )
                last_raw = response.text
                data = _extract_json_object(response.text)
                appeal = self._assemble_from_model(data, case, review, resolved_guideline)

                return AppealAgentResult(
                    appeal=appeal,
                    attempts=attempt,
                    backend=self.backend_name,
                    model=response.model,
                    used_ai=True,
                    repaired=attempt > 1,
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
                                "the required keys, including a complete "
                                "'letter_text'. No commentary."
                            ),
                        }
                    ]
                    continue

            except LLMError as exc:
                errors.append(f"Attempt {attempt}: LLMError: {exc}")
                appeal = self.builder.build(case, review, resolved_guideline)
                return AppealAgentResult(
                    appeal=appeal,
                    attempts=attempt,
                    backend=self.backend_name,
                    model=self.backend_name,
                    used_ai=False,
                    errors=errors,
                )

        # Exhausted retries: fall back to the deterministic builder.
        appeal = self.builder.build(case, review, resolved_guideline)
        return AppealAgentResult(
            appeal=appeal,
            attempts=self.max_retries,
            backend=self.backend_name,
            model=self.backend_name,
            used_ai=False,
            repaired=True,
            raw_text=last_raw,
            errors=errors,
        )

    def _assemble_from_model(
        self,
        data: dict,
        case: PatientCase,
        review: ReviewResult,
        guideline: ClinicalGuideline | None,
    ) -> AppealLetter:
        """Validate model output into an AppealLetter, stamping safe defaults.

        Identity fields (ids, names, codes) are sourced from the trusted
        PatientCase, NOT from the model, to prevent drift/fabrication. The
        model contributes the narrative fields and the letter body. If the
        model omitted ``letter_text``, we render it deterministically from the
        validated structured fields so the result is always complete.
        """
        appeal_id = new_appeal_id()
        created_at = datetime.now(timezone.utc).isoformat()
        original_decision = (
            case.decision.value if case.decision is not Decision.UNKNOWN else None
        )

        # Validate the model's narrative contributions through the pydantic
        # model (this also coerces/cleans types and enforces schema).
        candidate = AppealLetter.model_validate(
            {
                "appeal_id": appeal_id,
                "created_at": created_at,
                "patient_name": case.patient_name,
                "member_id": case.member_id,
                "insurance_company": case.insurance_company,
                "requested_service": case.requested_service,
                "original_decision": original_decision,
                "appeal_reason": data.get("appeal_reason", ""),
                "clinical_summary": data.get("clinical_summary", ""),
                "guideline_support": data.get("guideline_support", []),
                "missing_information": data.get("missing_information", []),
                "recommended_next_steps": data.get("recommended_next_steps", []),
                "letter_text": data.get("letter_text", ""),
                "confidence_score": data.get("confidence_score", 0.0),
            }
        )

        # Ensure a complete letter body: if the model didn't supply one, render
        # it deterministically from the (validated) structured fields.
        if not candidate.letter_text.strip():
            candidate.letter_text = render_letter_text(
                appeal_id=candidate.appeal_id,
                created_at=candidate.created_at,
                case=case,
                review=review,
                guideline=guideline,
                appeal_reason=candidate.appeal_reason,
                clinical_summary=candidate.clinical_summary,
                guideline_support=candidate.guideline_support,
                missing_information=candidate.missing_information,
                recommended_next_steps=candidate.recommended_next_steps,
            )

        if candidate.confidence_score <= 0.0:
            candidate.confidence_score = 0.6

        return candidate
