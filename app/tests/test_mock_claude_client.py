"""Tests for MockClaudeClient and the agent behavior it exercises.

These verify the validation layer, retry logic, and schema enforcement of the
extraction agent using a realistic Claude stand-in (rather than ad-hoc
scripted/regex doubles).
"""

from __future__ import annotations

import json

import pytest

from app.agents.medical_extraction_agent import (
    ExtractionError,
    MedicalExtractionAgent,
)
from app.models.patient_case import Decision, PatientCase
from app.services.mock_claude_client import (
    MockClaudeClient,
    MockScenario,
    _BASE_CASE,
)

DOC = "any document text suffices; the mock ignores content"


# --------------------------------------------------------------------------- #
# The mock itself
# --------------------------------------------------------------------------- #
class TestMockClientShape:
    def test_is_ai_true(self):
        client = MockClaudeClient(MockScenario.VALID)
        assert client.is_ai is True
        assert client.name == "mock-claude"

    def test_valid_scenario_is_parseable_json(self):
        client = MockClaudeClient(MockScenario.VALID)
        resp = client.complete(system="s", messages=[{"role": "user", "content": "x"}])
        data = json.loads(resp.text)
        assert data["patient_name"] == _BASE_CASE["patient_name"]
        assert resp.raw["scenario"] == "valid"

    def test_invalid_scenario_is_not_parseable(self):
        client = MockClaudeClient(MockScenario.INVALID_JSON)
        resp = client.complete(system="s", messages=[{"role": "user", "content": "x"}])
        with pytest.raises(json.JSONDecodeError):
            json.loads(resp.text)

    def test_markdown_scenario_has_fence(self):
        client = MockClaudeClient(MockScenario.MARKDOWN_JSON)
        resp = client.complete(system="s", messages=[{"role": "user", "content": "x"}])
        assert "```json" in resp.text

    def test_truncated_scenario_has_no_closing_brace(self):
        client = MockClaudeClient(MockScenario.TRUNCATED)
        resp = client.complete(system="s", messages=[{"role": "user", "content": "x"}])
        assert resp.text.count("{") > resp.text.count("}")

    def test_scenarios_consumed_in_order(self):
        client = MockClaudeClient([MockScenario.INVALID_JSON, MockScenario.VALID])
        r1 = client.complete(system="s", messages=[{"role": "user", "content": "x"}])
        r2 = client.complete(system="s", messages=[{"role": "user", "content": "x"}])
        assert r1.raw["scenario"] == "invalid_json"
        assert r2.raw["scenario"] == "valid"
        assert client.calls == 2

    def test_last_scenario_repeats_when_exhausted(self):
        client = MockClaudeClient([MockScenario.VALID])
        client.complete(system="s", messages=[{"role": "user", "content": "x"}])
        r2 = client.complete(system="s", messages=[{"role": "user", "content": "x"}])
        assert r2.raw["scenario"] == "valid"

    def test_raise_on_exhaustion(self):
        client = MockClaudeClient([MockScenario.VALID], raise_on_exhaustion=True)
        client.complete(system="s", messages=[{"role": "user", "content": "x"}])
        with pytest.raises(AssertionError):
            client.complete(system="s", messages=[{"role": "user", "content": "x"}])


# --------------------------------------------------------------------------- #
# Validation layer + schema enforcement via the agent
# --------------------------------------------------------------------------- #
class TestValidationLayer:
    def test_valid_first_try(self):
        agent = MedicalExtractionAgent(MockClaudeClient(MockScenario.VALID))
        result = agent.extract(DOC)
        assert isinstance(result.case, PatientCase)
        assert result.attempts == 1
        assert result.repaired is False
        assert result.case.decision is Decision.DENIED

    def test_missing_fields_handled_gracefully(self):
        agent = MedicalExtractionAgent(MockClaudeClient(MockScenario.MISSING_FIELDS))
        result = agent.extract(DOC)
        case = result.case
        # Omitted fields default to None / [] without raising.
        assert case.member_id is None
        assert case.date_of_birth is None
        assert case.cpt_codes == []
        assert case.physician_name is None
        # Present fields still populated.
        assert case.patient_name == _BASE_CASE["patient_name"]

    def test_markdown_wrapped_json_is_parsed(self):
        agent = MedicalExtractionAgent(MockClaudeClient(MockScenario.MARKDOWN_JSON))
        result = agent.extract(DOC)
        assert result.attempts == 1  # parsed on first try via fence extraction
        assert result.case.patient_name == _BASE_CASE["patient_name"]

    def test_hallucinated_fields_are_ignored(self):
        agent = MedicalExtractionAgent(MockClaudeClient(MockScenario.HALLUCINATED))
        result = agent.extract(DOC)
        case = result.case
        # Extra/invented keys must not appear on the validated model.
        assert not hasattr(case, "blood_type")
        assert "blood_type" not in case.model_dump()
        assert "lucky_number" not in case.model_dump()
        assert case.patient_name == _BASE_CASE["patient_name"]


# --------------------------------------------------------------------------- #
# Retry logic / invalid JSON recovery
# --------------------------------------------------------------------------- #
class TestRetryLogic:
    def test_invalid_then_valid_recovers(self):
        client = MockClaudeClient([MockScenario.INVALID_JSON, MockScenario.VALID])
        agent = MedicalExtractionAgent(client, max_retries=3)
        result = agent.extract(DOC)
        assert result.attempts == 2
        assert result.repaired is True
        assert client.calls == 2

    def test_truncated_then_valid_recovers(self):
        client = MockClaudeClient([MockScenario.TRUNCATED, MockScenario.VALID])
        agent = MedicalExtractionAgent(client, max_retries=3)
        result = agent.extract(DOC)
        assert result.attempts == 2
        assert result.repaired is True

    def test_prose_then_valid_recovers(self):
        client = MockClaudeClient([MockScenario.PROSE, MockScenario.VALID])
        agent = MedicalExtractionAgent(client, max_retries=3)
        result = agent.extract(DOC)
        assert result.attempts == 2

    def test_empty_then_valid_recovers(self):
        client = MockClaudeClient([MockScenario.EMPTY, MockScenario.VALID])
        agent = MedicalExtractionAgent(client, max_retries=3)
        result = agent.extract(DOC)
        assert result.attempts == 2

    def test_retry_prompt_is_appended(self):
        client = MockClaudeClient([MockScenario.INVALID_JSON, MockScenario.VALID])
        agent = MedicalExtractionAgent(client, max_retries=3)
        agent.extract(DOC)
        # Second call should have more messages than the first (repair turn).
        assert len(client.received_messages[1]) > len(client.received_messages[0])
        assert "valid json" in client.received_messages[1][-1]["content"].lower()

    def test_exhausts_retries_raises(self):
        client = MockClaudeClient(
            [MockScenario.INVALID_JSON, MockScenario.TRUNCATED, MockScenario.PROSE]
        )
        agent = MedicalExtractionAgent(client, max_retries=3)
        with pytest.raises(ExtractionError):
            agent.extract(DOC)
        assert client.calls == 3

    def test_respects_max_retries_setting(self):
        client = MockClaudeClient(
            [MockScenario.INVALID_JSON, MockScenario.VALID], raise_on_exhaustion=True
        )
        # With only 1 attempt allowed, the first invalid response should fail.
        agent = MedicalExtractionAgent(client, max_retries=1)
        with pytest.raises(ExtractionError):
            agent.extract(DOC)
        assert client.calls == 1
