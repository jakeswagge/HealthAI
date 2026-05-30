"""Tests for the GuidelineReviewAgent (AI-backed + offline fallback)."""

from __future__ import annotations

import json

from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.review.review_agent import GuidelineReviewAgent
from app.services.llm_client import LLMClient, LLMError, LLMResponse
from app.services.local_client import LocalHeuristicClient


class ScriptedAIClient(LLMClient):
    name = "scripted-ai"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    @property
    def is_ai(self) -> bool:
        return True

    def complete(self, *, system, messages, max_tokens=1200, temperature=0.0):
        text = self._responses[self.calls]
        self.calls += 1
        return LLMResponse(text=text, model="scripted-ai-model")


class BoomAIClient(LLMClient):
    name = "boom-ai"

    @property
    def is_ai(self) -> bool:
        return True

    def complete(self, *, system, messages, max_tokens=1200, temperature=0.0):
        raise LLMError("ai backend down")


VALID_REVIEW = json.dumps(
    {
        "recommendation": "DENY",
        "matched_criteria": ["Diagnosis confirmed"],
        "missing_criteria": ["Step therapy with a conventional DMARD"],
        "missing_evidence": ["Records of methotrexate trial and failure"],
        "recommended_actions": ["Submit step therapy documentation"],
        "contraindications_found": [],
        "rationale": "Step therapy not documented.",
        "confidence_score": 0.88,
    }
)

HUMIRA_DENIAL_CASE = PatientCase(
    diagnosis="Rheumatoid arthritis",
    requested_service="Humira (adalimumab)",
    decision=Decision.DENIED,
    denial_reason="Step therapy not met: no methotrexate (DMARD) trial.",
)


class TestOfflineFallback:
    def test_local_backend_uses_deterministic_engine(self):
        agent = GuidelineReviewAgent(llm_client=LocalHeuristicClient())
        out = agent.review(HUMIRA_DENIAL_CASE)
        assert out.used_ai is False
        assert isinstance(out.result, ReviewResult)
        assert out.result.recommendation is Recommendation.DENY
        assert out.guideline_id == "GL-HUMIRA-001"


class TestAIBackend:
    def test_valid_first_try(self):
        agent = GuidelineReviewAgent(llm_client=ScriptedAIClient([VALID_REVIEW]))
        out = agent.review(HUMIRA_DENIAL_CASE)
        assert out.used_ai is True
        assert out.attempts == 1
        assert out.result.recommendation is Recommendation.DENY
        assert out.result.guideline_id == "GL-HUMIRA-001"

    def test_retry_then_success(self):
        client = ScriptedAIClient(["not json", VALID_REVIEW])
        agent = GuidelineReviewAgent(llm_client=client)
        out = agent.review(HUMIRA_DENIAL_CASE)
        assert out.attempts == 2
        assert out.repaired is True
        assert out.result.recommendation is Recommendation.DENY

    def test_exhausted_retries_fall_back_to_engine(self):
        client = ScriptedAIClient(["no", "still no", "nope"])
        agent = GuidelineReviewAgent(llm_client=client, max_retries=3)
        out = agent.review(HUMIRA_DENIAL_CASE)
        # Falls back deterministically rather than raising.
        assert out.used_ai is False
        assert isinstance(out.result, ReviewResult)
        assert out.result.recommendation is Recommendation.DENY

    def test_backend_error_falls_back(self):
        agent = GuidelineReviewAgent(llm_client=BoomAIClient())
        out = agent.review(HUMIRA_DENIAL_CASE)
        assert out.used_ai is False
        assert isinstance(out.result, ReviewResult)


class TestNoGuideline:
    def test_unmatched_service(self):
        agent = GuidelineReviewAgent(llm_client=ScriptedAIClient([VALID_REVIEW]))
        out = agent.review(PatientCase(requested_service="dental cleaning"))
        assert out.guideline_id is None
        assert out.result.recommendation is Recommendation.INSUFFICIENT_INFORMATION
