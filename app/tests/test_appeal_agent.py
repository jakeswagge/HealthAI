"""Tests for AppealGenerationAgent (AI path + offline fallback + retry)."""

from __future__ import annotations

import json

from app.appeals.appeal_agent import AppealGenerationAgent
from app.guidelines.repository import get_default_repository
from app.models.appeal_letter import AppealLetter
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.review.engine import ClinicalReviewEngine
from app.services.local_client import LocalHeuristicClient
from app.services.mock_claude_client import MockClaudeClient, MockScenario

HUMIRA_DENIAL = PatientCase(
    patient_name="Harold T. Greene",
    member_id="WP-558210334",
    diagnosis="Moderate to severe rheumatoid arthritis",
    icd10_codes=["M06.9"],
    requested_service="Humira (adalimumab)",
    cpt_codes=["J0135"],
    insurance_company="WellPoint National Insurance",
    decision=Decision.DENIED,
    denial_reason="Step therapy not met: no documented DMARD (methotrexate) trial.",
    physician_name="Dr. Susan A. Patel, MD",
)


def _review() -> ReviewResult:
    return ClinicalReviewEngine().review(HUMIRA_DENIAL)


# A realistic appeal-shaped JSON payload for the mock Claude client.
APPEAL_PAYLOAD = {
    "appeal_reason": "The denial cited an unmet step-therapy requirement; we request reconsideration.",
    "clinical_summary": "The member carries a documented diagnosis of rheumatoid arthritis.",
    "guideline_support": ["GL-HUMIRA-001 supports biologic therapy after DMARD failure."],
    "missing_information": ["Documentation was not available for the DMARD trial."],
    "recommended_next_steps": ["Submit methotrexate trial records."],
    "confidence_score": 0.82,
    "letter_text": (
        "# Prior Authorization Appeal Letter\n\n"
        "## Patient Information\n- Patient Name: Harold T. Greene\n\n"
        "## Clinical Background\nRheumatoid arthritis.\n\n"
        "## Requested Service\nHumira (adalimumab)\n\n"
        "## Reason For Appeal\nStep therapy challenge.\n\n"
        "## Guideline Support\n- GL-HUMIRA-001\n\n"
        "## Missing Evidence\n- Documentation was not available for the DMARD trial.\n\n"
        "## Request For Reconsideration\nPlease reconsider.\n\n"
        "## Signature\n[Provider Name]\n"
    ),
}

# Payload missing letter_text -> agent must render it deterministically.
APPEAL_PAYLOAD_NO_LETTER = {
    k: v for k, v in APPEAL_PAYLOAD.items() if k != "letter_text"
}


def _appeal_mock(scenarios, base=None):
    return MockClaudeClient(scenarios, base_case=base or APPEAL_PAYLOAD)


class TestOfflineFallback:
    def test_local_backend_uses_builder(self):
        agent = AppealGenerationAgent(llm_client=LocalHeuristicClient())
        out = agent.generate(HUMIRA_DENIAL, _review())
        assert out.used_ai is False
        assert isinstance(out.appeal, AppealLetter)
        assert out.appeal.has_letter
        assert out.appeal.original_decision == "denied"


class TestAIPath:
    def test_valid_first_try(self):
        agent = AppealGenerationAgent(llm_client=_appeal_mock(MockScenario.VALID))
        out = agent.generate(HUMIRA_DENIAL, _review())
        assert out.used_ai is True
        assert out.attempts == 1
        assert isinstance(out.appeal, AppealLetter)
        # Identity is sourced from the trusted case, not the model.
        assert out.appeal.patient_name == "Harold T. Greene"
        assert out.appeal.member_id == "WP-558210334"
        assert out.appeal.has_letter

    def test_markdown_wrapped_is_parsed(self):
        agent = AppealGenerationAgent(llm_client=_appeal_mock(MockScenario.MARKDOWN_JSON))
        out = agent.generate(HUMIRA_DENIAL, _review())
        assert out.attempts == 1
        assert out.appeal.has_letter

    def test_missing_letter_text_is_rendered(self):
        agent = AppealGenerationAgent(
            llm_client=_appeal_mock(MockScenario.VALID, base=APPEAL_PAYLOAD_NO_LETTER)
        )
        out = agent.generate(HUMIRA_DENIAL, _review())
        # The agent fills in a complete letter from structured fields.
        assert out.appeal.has_letter
        assert "## Patient Information" in out.appeal.letter_text
        assert "## Signature" in out.appeal.letter_text

    def test_hallucinated_keys_ignored(self):
        agent = AppealGenerationAgent(llm_client=_appeal_mock(MockScenario.HALLUCINATED))
        out = agent.generate(HUMIRA_DENIAL, _review())
        dumped = out.appeal.model_dump()
        assert "blood_type" not in dumped
        assert "lucky_number" not in dumped


class TestRetryLogic:
    def test_invalid_then_valid(self):
        client = _appeal_mock([MockScenario.INVALID_JSON, MockScenario.VALID])
        agent = AppealGenerationAgent(llm_client=client, max_retries=3)
        out = agent.generate(HUMIRA_DENIAL, _review())
        assert out.attempts == 2
        assert out.repaired is True
        assert out.used_ai is True

    def test_truncated_then_valid(self):
        client = _appeal_mock([MockScenario.TRUNCATED, MockScenario.VALID])
        agent = AppealGenerationAgent(llm_client=client, max_retries=3)
        out = agent.generate(HUMIRA_DENIAL, _review())
        assert out.attempts == 2

    def test_retry_prompt_appended(self):
        client = _appeal_mock([MockScenario.INVALID_JSON, MockScenario.VALID])
        agent = AppealGenerationAgent(llm_client=client, max_retries=3)
        agent.generate(HUMIRA_DENIAL, _review())
        assert len(client.received_messages[1]) > len(client.received_messages[0])
        assert "valid json" in client.received_messages[1][-1]["content"].lower()

    def test_exhausted_retries_fall_back_to_builder(self):
        client = _appeal_mock(
            [MockScenario.INVALID_JSON, MockScenario.PROSE, MockScenario.TRUNCATED]
        )
        agent = AppealGenerationAgent(llm_client=client, max_retries=3)
        out = agent.generate(HUMIRA_DENIAL, _review())
        # Degrades to deterministic builder rather than raising.
        assert out.used_ai is False
        assert isinstance(out.appeal, AppealLetter)
        assert out.appeal.has_letter


class TestSchemaCompliance:
    def test_output_is_valid_appeal_letter(self):
        agent = AppealGenerationAgent(llm_client=_appeal_mock(MockScenario.VALID))
        out = agent.generate(HUMIRA_DENIAL, _review())
        # Round-trips through pydantic without error.
        restored = AppealLetter.model_validate(out.appeal.model_dump())
        assert restored.appeal_id == out.appeal.appeal_id

    def test_guideline_resolved_from_review_id(self):
        review = _review()
        assert review.guideline_id == "GL-HUMIRA-001"
        agent = AppealGenerationAgent(llm_client=LocalHeuristicClient())
        out = agent.generate(HUMIRA_DENIAL, review)
        assert any("GL-HUMIRA-001" in s for s in out.appeal.guideline_support)
