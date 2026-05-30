"""Unit tests for MedicalExtractionAgent: JSON parsing, retries, validation."""

from __future__ import annotations

import json

import pytest

from app.agents.medical_extraction_agent import (
    ExtractionError,
    MedicalExtractionAgent,
    _extract_json_object,
)
from app.models.patient_case import Decision, PatientCase
from app.services.llm_client import LLMClient, LLMError, LLMResponse


# --------------------------------------------------------------------------- #
# Scripted fake backends for deterministic agent testing
# --------------------------------------------------------------------------- #
class ScriptedClient(LLMClient):
    """Returns a preset list of responses, one per call."""

    name = "scripted"

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    @property
    def is_ai(self) -> bool:
        return True

    def complete(self, *, system, messages, max_tokens=1500, temperature=0.0):
        if self.calls >= len(self._responses):
            raise AssertionError("ScriptedClient ran out of responses.")
        text = self._responses[self.calls]
        self.calls += 1
        return LLMResponse(text=text, model="scripted-model")


class BoomClient(LLMClient):
    name = "boom"

    @property
    def is_ai(self) -> bool:
        return True

    def complete(self, *, system, messages, max_tokens=1500, temperature=0.0):
        raise LLMError("backend exploded")


VALID_JSON = json.dumps(
    {
        "patient_name": "Jane Roe",
        "member_id": "X123",
        "date_of_birth": "01/02/1990",
        "diagnosis": "Test",
        "icd10_codes": ["A00.0"],
        "requested_service": "MRI",
        "cpt_codes": ["12345"],
        "insurance_company": "Acme",
        "decision": "denied",
        "denial_reason": "Not necessary",
        "physician_name": "Dr Who",
        "confidence_score": 0.9,
    }
)


class TestJsonObjectExtraction:
    def test_plain_json(self):
        assert _extract_json_object('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        text = "```json\n{\"a\": 1}\n```"
        assert _extract_json_object(text) == {"a": 1}

    def test_json_embedded_in_prose(self):
        text = 'Sure! Here you go:\n{"a": 1, "b": [2,3]}\nHope that helps.'
        assert _extract_json_object(text) == {"a": 1, "b": [2, 3]}

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            _extract_json_object("there is no json here")


class TestAgentHappyPath:
    def test_first_try_success(self):
        agent = MedicalExtractionAgent(ScriptedClient([VALID_JSON]), max_retries=3)
        result = agent.extract("some document text")
        assert isinstance(result.case, PatientCase)
        assert result.case.decision is Decision.DENIED
        assert result.attempts == 1
        assert result.repaired is False
        assert result.case.confidence_score == 0.9


class TestAgentRetry:
    def test_recovers_after_bad_then_good(self):
        client = ScriptedClient(["not json at all", VALID_JSON])
        agent = MedicalExtractionAgent(client, max_retries=3)
        result = agent.extract("doc")
        assert result.attempts == 2
        assert result.repaired is True
        assert len(result.errors) == 1
        assert client.calls == 2

    def test_recovers_from_schema_violation(self):
        bad = json.dumps({"decision": "denied", "confidence_score": "not-a-number-but-coerced"})
        # confidence coerces to 0.0; this is actually valid. Use a truly invalid
        # structure instead: decision as a list is coerced too, so force a type
        # error via icd10_codes being a non-iterable int handled by validator...
        # Simplest hard failure: feed a JSON array (not an object).
        client = ScriptedClient(["[1, 2, 3]", VALID_JSON])
        agent = MedicalExtractionAgent(client, max_retries=3)
        result = agent.extract("doc")
        assert result.attempts == 2
        assert result.repaired is True

    def test_exhausts_retries_and_raises(self):
        client = ScriptedClient(["nope", "still nope", "nope again"])
        agent = MedicalExtractionAgent(client, max_retries=3)
        with pytest.raises(ExtractionError):
            agent.extract("doc")
        assert client.calls == 3


class TestAgentErrors:
    def test_empty_document_raises(self):
        agent = MedicalExtractionAgent(ScriptedClient([VALID_JSON]))
        with pytest.raises(ExtractionError):
            agent.extract("   ")

    def test_backend_error_raises_extraction_error(self):
        agent = MedicalExtractionAgent(BoomClient(), max_retries=3)
        with pytest.raises(ExtractionError):
            agent.extract("doc")


class TestConfidenceFallback:
    def test_zero_confidence_replaced_by_completeness(self):
        data = json.loads(VALID_JSON)
        data["confidence_score"] = 0.0
        client = ScriptedClient([json.dumps(data)])
        agent = MedicalExtractionAgent(client)
        result = agent.extract("doc")
        # Completeness-based fallback should be > 0.
        assert result.case.confidence_score > 0.0
