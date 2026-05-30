"""Review agent against realistic Claude mock responses.

Verifies the review agent's JSON validation, retry, and schema enforcement
using a MockClaudeClient configured to emit ReviewResult-shaped payloads.
"""

from __future__ import annotations

import json

from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.review.review_agent import GuidelineReviewAgent
from app.services.mock_claude_client import MockClaudeClient, MockScenario

HUMIRA_DENIAL_CASE = PatientCase(
    diagnosis="Rheumatoid arthritis",
    requested_service="Humira (adalimumab)",
    decision=Decision.DENIED,
    denial_reason="Step therapy not met: no methotrexate (DMARD) trial.",
)

VALID_REVIEW = {
    "recommendation": "DENY",
    "matched_criteria": ["Diagnosis confirmed"],
    "missing_criteria": ["Step therapy with a conventional DMARD"],
    "missing_evidence": ["Records of methotrexate trial and failure"],
    "recommended_actions": ["Submit step therapy documentation"],
    "contraindications_found": [],
    "rationale": "Step therapy not documented.",
    "confidence_score": 0.88,
}


def _review_mock(scenarios):
    """A MockClaudeClient whose base payload is a ReviewResult."""
    return MockClaudeClient(scenarios, base_case=VALID_REVIEW)


class TestReviewValidation:
    def test_valid_review_first_try(self):
        agent = GuidelineReviewAgent(llm_client=_review_mock(MockScenario.VALID))
        out = agent.review(HUMIRA_DENIAL_CASE)
        assert out.used_ai is True
        assert out.attempts == 1
        assert isinstance(out.result, ReviewResult)
        assert out.result.recommendation is Recommendation.DENY
        assert out.result.guideline_id == "GL-HUMIRA-001"

    def test_markdown_wrapped_review_parsed(self):
        agent = GuidelineReviewAgent(llm_client=_review_mock(MockScenario.MARKDOWN_JSON))
        out = agent.review(HUMIRA_DENIAL_CASE)
        assert out.attempts == 1
        assert out.result.recommendation is Recommendation.DENY

    def test_hallucinated_keys_ignored(self):
        agent = GuidelineReviewAgent(llm_client=_review_mock(MockScenario.HALLUCINATED))
        out = agent.review(HUMIRA_DENIAL_CASE)
        dumped = out.result.model_dump()
        assert "blood_type" not in dumped
        assert out.result.recommendation is Recommendation.DENY


class TestReviewRetry:
    def test_invalid_then_valid(self):
        client = _review_mock([MockScenario.INVALID_JSON, MockScenario.VALID])
        agent = GuidelineReviewAgent(llm_client=client, max_retries=3)
        out = agent.review(HUMIRA_DENIAL_CASE)
        assert out.attempts == 2
        assert out.repaired is True
        assert out.result.recommendation is Recommendation.DENY

    def test_truncated_then_valid(self):
        client = _review_mock([MockScenario.TRUNCATED, MockScenario.VALID])
        agent = GuidelineReviewAgent(llm_client=client, max_retries=3)
        out = agent.review(HUMIRA_DENIAL_CASE)
        assert out.attempts == 2

    def test_exhausted_retries_falls_back_to_engine(self):
        # All invalid -> agent degrades to deterministic engine (still DENY).
        client = _review_mock(
            [MockScenario.INVALID_JSON, MockScenario.PROSE, MockScenario.TRUNCATED]
        )
        agent = GuidelineReviewAgent(llm_client=client, max_retries=3)
        out = agent.review(HUMIRA_DENIAL_CASE)
        assert out.used_ai is False
        assert isinstance(out.result, ReviewResult)
        assert out.result.recommendation is Recommendation.DENY
