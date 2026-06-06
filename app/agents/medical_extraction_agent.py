"""MedicalExtractionAgent: raw document text -> validated :class:`PatientCase`.

Responsibilities:
- Build the extraction prompt (see :mod:`app.agents.prompts`).
- Call the LLM via the service layer (:class:`app.services.LLMClient`).
- Parse the model's JSON output robustly.
- Validate against the :class:`PatientCase` pydantic schema.
- Retry up to N times (default 3) when parsing or validation fails, feeding
  the error back to the model so it can self-correct.
- Always return a confidence score, and handle missing fields gracefully.

The agent never imports an AI SDK directly; all model access goes through the
injected :class:`LLMClient`, keeping AI isolated behind the service layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError as PydanticValidationError

from app.agents.prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_messages
from app.models.patient_case import PatientCase
from app.services.factory import get_llm_client
from app.services.json_utils import extract_json_object as _extract_json_object
from app.services.llm_client import LLMClient, LLMError


class ExtractionError(Exception):
    """Raised when extraction fails after all retries."""


@dataclass
class ExtractionResult:
    """Outcome of an extraction attempt.

    Attributes:
        case: The validated :class:`PatientCase`.
        attempts: How many model calls were made.
        backend: Name of the backend that produced the result.
        model: Model identifier.
        repaired: True if a retry was needed before success.
        raw_text: The final raw model output (for debugging/inspection).
        errors: Per-attempt error messages (empty if first try succeeded).
    """

    case: PatientCase
    attempts: int
    backend: str
    model: str
    repaired: bool = False
    raw_text: str = ""
    errors: list[str] = field(default_factory=list)


# ``_extract_json_object`` is re-exported from app.services.json_utils (shared
# implementation, Milestone 12). The alias preserves the prior public name used
# by tests; behavior is unchanged.


class MedicalExtractionAgent:
    """Extracts a structured :class:`PatientCase` from raw document text."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        max_retries: int = 3,
        max_tokens: int = 1500,
    ) -> None:
        """Create the agent.

        Args:
            llm_client: Backend to use. Defaults to the auto-detected client
                from the service-layer factory (Claude if configured, else the
                local heuristic backend).
            max_retries: Total number of attempts (>= 1). Defaults to 3.
            max_tokens: Max tokens to request from the model per call.
        """
        self.llm = llm_client or get_llm_client()
        self.max_retries = max(1, max_retries)
        self.max_tokens = max_tokens

    @property
    def backend_name(self) -> str:
        return getattr(self.llm, "name", "unknown")

    def extract(self, document_text: str) -> ExtractionResult:
        """Extract structured data from document text.

        Args:
            document_text: Raw text of the insurance document.

        Returns:
            An :class:`ExtractionResult` containing a validated PatientCase.

        Raises:
            ExtractionError: If no valid case could be produced after retries.
        """
        if not document_text or not document_text.strip():
            raise ExtractionError("Document text is empty; nothing to extract.")

        messages = build_extraction_messages(document_text)
        errors: list[str] = []
        last_raw = ""

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.llm.complete(
                    system=EXTRACTION_SYSTEM_PROMPT,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=0.0,
                )
                last_raw = response.text
                data = _extract_json_object(response.text)
                case = PatientCase.model_validate(data)

                # Ensure a usable confidence score: if the model returned 0 or
                # omitted it, fall back to completeness-based confidence.
                if case.confidence_score <= 0.0:
                    case.confidence_score = max(0.05, case.completeness)

                return ExtractionResult(
                    case=case,
                    attempts=attempt,
                    backend=self.backend_name,
                    model=response.model,
                    repaired=attempt > 1,
                    raw_text=last_raw,
                    errors=errors,
                )

            except (ValueError, PydanticValidationError) as exc:
                # Parsing or schema validation failed: log and ask for a fix.
                msg = f"Attempt {attempt}: {type(exc).__name__}: {exc}"
                errors.append(msg)
                if attempt < self.max_retries:
                    messages = messages + [
                        {
                            "role": "user",
                            "content": (
                                "Your previous response was not valid against "
                                "the required schema. Error: "
                                f"{exc}. Respond again with VALID JSON ONLY "
                                "containing exactly the required keys. Use null "
                                "or [] for unknown values. No commentary."
                            ),
                        }
                    ]
                    continue

            except LLMError as exc:
                # Backend failure: not recoverable by re-prompting.
                errors.append(f"Attempt {attempt}: LLMError: {exc}")
                raise ExtractionError(
                    f"LLM backend failed: {exc}"
                ) from exc

        raise ExtractionError(
            "Failed to produce a valid PatientCase after "
            f"{self.max_retries} attempts. Errors: {errors}. "
            f"Last raw output: {last_raw[:500]!r}"
        )
