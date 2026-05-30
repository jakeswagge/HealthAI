"""MockClaudeClient: a realistic Claude stand-in for tests.

This replaces ad-hoc scripted/regex test doubles with a single, reusable mock
that mimics how a real Claude model behaves over the LLM service interface. It
reports ``is_ai = True`` (it impersonates a real model), so the extraction and
review agents exercise their *full* AI code path against it: JSON parsing,
schema validation, and retry/repair logic.

The mock is driven by scenarios that reproduce the messy realities of LLM
output:

- ``VALID``           a clean JSON object with all fields
- ``MISSING_FIELDS``  valid JSON omitting several keys (graceful defaults)
- ``INVALID_JSON``    malformed JSON (syntax error) -> should trigger retry
- ``MARKDOWN_JSON``   JSON wrapped in a ```json ... ``` fence + prose
- ``HALLUCINATED``    valid JSON plus invented keys not in the schema
- ``TRUNCATED``       JSON cut off mid-object (no closing brace) -> retry
- ``EMPTY``           empty/whitespace output -> retry
- ``PROSE``           natural-language answer with no JSON -> retry

A scenario list is consumed one entry per ``complete()`` call, which makes it
trivial to script multi-attempt retry sequences, e.g.
``[INVALID_JSON, VALID]`` to assert the agent recovers on the second attempt.
"""

from __future__ import annotations

import json
from enum import Enum

from app.services.llm_client import LLMClient, LLMError, LLMResponse


class MockScenario(str, Enum):
    """Categories of realistic Claude responses."""

    VALID = "valid"
    MISSING_FIELDS = "missing_fields"
    INVALID_JSON = "invalid_json"
    MARKDOWN_JSON = "markdown_json"
    HALLUCINATED = "hallucinated"
    TRUNCATED = "truncated"
    EMPTY = "empty"
    PROSE = "prose"


# A realistic, fully-populated extraction payload used as the basis for the
# "happy" scenarios. Mirrors the PatientCase JSON contract.
_BASE_CASE: dict = {
    "patient_name": "Harold T. Greene",
    "member_id": "WP-558210334",
    "date_of_birth": "04/17/1971",
    "diagnosis": "Moderate to severe rheumatoid arthritis",
    "icd10_codes": ["M06.9"],
    "requested_service": "Humira (adalimumab)",
    "cpt_codes": ["J0135"],
    "insurance_company": "WellPoint National Insurance",
    "decision": "denied",
    "denial_reason": "Step therapy requirement not met: no documented trial of a conventional DMARD.",
    "physician_name": "Dr. Susan A. Patel, MD",
    "confidence_score": 0.93,
}


def _valid_response(base: dict) -> str:
    return json.dumps(base, indent=2)


def _missing_fields_response(base: dict) -> str:
    # A realistic case where Claude could not find several values and correctly
    # returns nulls / empty lists for them.
    partial = dict(base)
    partial["member_id"] = None
    partial["date_of_birth"] = None
    partial["cpt_codes"] = []
    partial["physician_name"] = None
    return json.dumps(partial, indent=2)


def _invalid_json_response(base: dict) -> str:
    # Trailing comma + unquoted token: invalid JSON that json.loads rejects.
    return (
        "{\n"
        '  "patient_name": "Harold T. Greene",\n'
        '  "member_id": "WP-558210334",\n'
        '  "decision": denied,\n'  # unquoted value
        '  "confidence_score": 0.9,\n'  # trailing comma
        "}"
    )


def _markdown_json_response(base: dict) -> str:
    # Claude sometimes wraps JSON in a fenced block with a little prose.
    body = json.dumps(base, indent=2)
    return (
        "Here is the structured extraction you requested:\n\n"
        f"```json\n{body}\n```\n\n"
        "Let me know if you need anything else."
    )


def _hallucinated_response(base: dict) -> str:
    # Valid JSON, but with invented keys not present in the schema. pydantic
    # should ignore the extras; the agent must not surface them.
    payload = dict(base)
    payload["blood_type"] = "O+"
    payload["lucky_number"] = 7
    payload["secondary_diagnosis"] = "fabricated value"
    return json.dumps(payload, indent=2)


def _truncated_response(base: dict) -> str:
    # Output cut off mid-object (e.g., max_tokens hit): no closing brace.
    full = json.dumps(base, indent=2)
    return full[: int(len(full) * 0.6)]


def _render(scenario: MockScenario, base: dict) -> str:
    if scenario is MockScenario.VALID:
        return _valid_response(base)
    if scenario is MockScenario.MISSING_FIELDS:
        return _missing_fields_response(base)
    if scenario is MockScenario.INVALID_JSON:
        return _invalid_json_response(base)
    if scenario is MockScenario.MARKDOWN_JSON:
        return _markdown_json_response(base)
    if scenario is MockScenario.HALLUCINATED:
        return _hallucinated_response(base)
    if scenario is MockScenario.TRUNCATED:
        return _truncated_response(base)
    if scenario is MockScenario.EMPTY:
        return "   "
    if scenario is MockScenario.PROSE:
        return (
            "I reviewed the document. It appears to be a denial for Humira due "
            "to an unmet step-therapy requirement, but I won't return JSON."
        )
    raise ValueError(f"Unknown scenario: {scenario}")


class MockClaudeClient(LLMClient):
    """A scripted, realistic Claude stand-in implementing :class:`LLMClient`."""

    name = "mock-claude"

    def __init__(
        self,
        scenarios: "MockScenario | list[MockScenario] | str | list[str]",
        model: str = "claude-mock-1",
        base_case: dict | None = None,
        raise_on_exhaustion: bool = False,
    ) -> None:
        """Create the mock.

        Args:
            scenarios: A single scenario or a list consumed one-per-call. The
                last scenario is reused if more calls occur than scenarios,
                unless ``raise_on_exhaustion`` is True.
            model: Reported model id.
            base_case: Optional override for the base extraction payload.
            raise_on_exhaustion: If True, raise once scenarios are exhausted
                instead of repeating the last one.
        """
        if isinstance(scenarios, (MockScenario, str)):
            scenarios = [scenarios]
        self._scenarios: list[MockScenario] = [
            s if isinstance(s, MockScenario) else MockScenario(s) for s in scenarios
        ]
        if not self._scenarios:
            raise ValueError("At least one scenario is required.")
        self.model = model
        self._base = dict(base_case or _BASE_CASE)
        self._raise_on_exhaustion = raise_on_exhaustion
        self.calls = 0
        self.received_messages: list[list[dict[str, str]]] = []

    @property
    def is_ai(self) -> bool:
        # Impersonates a real model so agents take the full AI path.
        return True

    def _next_scenario(self) -> MockScenario:
        idx = self.calls
        if idx < len(self._scenarios):
            return self._scenarios[idx]
        if self._raise_on_exhaustion:
            raise AssertionError(
                "MockClaudeClient ran out of scripted scenarios."
            )
        return self._scenarios[-1]

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> LLMResponse:
        scenario = self._next_scenario()
        self.calls += 1
        # Record messages so tests can assert retry prompts were appended.
        self.received_messages.append(list(messages))
        text = _render(scenario, self._base)
        return LLMResponse(
            text=text,
            model=self.model,
            raw={"scenario": scenario.value, "call": self.calls},
        )
