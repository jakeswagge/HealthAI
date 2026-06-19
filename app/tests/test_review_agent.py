"""Tests for the GuidelineReviewAgent (AI-backed + offline fallback)."""

from __future__ import annotations

import json

from app.models.patient_case import Decision, PatientCase
from app.models.review_result import CriterionStatus, Recommendation, ReviewResult
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

AI_SELECTED_REVIEW = json.dumps(
    {
        "guideline_id": "GL-HUMIRA-001",
        "service_name": "Humira (adalimumab)",
        "recommendation": "DENY",
        "matched_criteria": ["Diagnosis confirmed"],
        "missing_criteria": ["Step therapy with a conventional DMARD"],
        "missing_evidence": ["Records of methotrexate trial and failure"],
        "recommended_actions": ["Submit step therapy documentation"],
        "contraindications_found": [],
        "rationale": "The supplied local Humira guideline applies.",
        "confidence_score": 0.82,
    }
)

AI_APPROVE_REVIEW = json.dumps(
    {
        "guideline_id": "GL-HUMIRA-001",
        "service_name": "Humira (adalimumab)",
        "recommendation": "APPROVE",
        "matched_criteria": [
            "Documented diagnosis of moderate-to-severe rheumatoid arthritis (or other approved indication).",
            "Trial and failure of at least one conventional DMARD (e.g., methotrexate) for 3 months (step therapy).",
            "Negative tuberculosis (TB) screening prior to initiating therapy.",
            "Prescribed by or in consultation with a rheumatologist or appropriate specialist.",
        ],
        "missing_criteria": [],
        "missing_evidence": [],
        "recommended_actions": [],
        "contraindications_found": [],
        "rationale": "AI approved all criteria.",
        "confidence_score": 0.91,
    }
)

AI_HISTORICAL_ARCHIVE_REVIEW_WITH_DETAILS = json.dumps(
    {
        "guideline_id": "GL-HUMIRA-001",
        "service_name": "Humira (adalimumab)",
        "recommendation": "APPROVE",
        "matched_criteria": [
            "Documented diagnosis of moderate-to-severe rheumatoid arthritis (or other approved indication).",
            "Trial and failure of at least one conventional DMARD (e.g., methotrexate) for 3 months (step therapy).",
            "Negative tuberculosis (TB) screening prior to initiating therapy.",
            "Prescribed by or in consultation with a rheumatologist or appropriate specialist.",
        ],
        "missing_criteria": [],
        "missing_evidence": [],
        "recommended_actions": [],
        "contraindications_found": [],
        "criteria_detail": [
            {
                "id": "DX_CONFIRMED",
                "description": "Documented diagnosis of moderate-to-severe rheumatoid arthritis (or other approved indication).",
                "met": True,
                "status": "met",
                "reasoning": "Severe plaque psoriasis is documented as an approved indication.",
                "confidence_score": 0.9,
            },
            {
                "id": "STEP_THERAPY",
                "description": "Trial and failure of at least one conventional DMARD (e.g., methotrexate) for 3 months (step therapy).",
                "met": True,
                "status": "met",
                "reasoning": "A 16-week methotrexate failure is documented.",
                "confidence_score": 0.88,
            },
            {
                "id": "TB_SCREEN",
                "description": "Negative tuberculosis (TB) screening prior to initiating therapy.",
                "met": False,
                "status": "unknown",
                "missing_evidence": ["Current negative TB screening within the required lookback window."],
                "reasoning": "The only negative PPD is from 2021 and is stale for the 2026 request.",
                "confidence_score": 0.9,
            },
            {
                "id": "SPECIALIST",
                "description": "Prescribed by or in consultation with a rheumatologist or appropriate specialist.",
                "met": False,
                "status": "unknown",
                "missing_evidence": ["Current specialist prescription or consultation note."],
                "reasoning": "Dermatology Clinic Coordinator is administrative context, not specialist-prescriber evidence.",
                "confidence_score": 0.9,
            },
        ],
        "rationale": "The model detail says current TB and specialist evidence are unknown.",
        "confidence_score": 0.86,
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
        assert out.result.generated_by_ai is True
        assert out.result.review_backend == "scripted-ai"
        assert out.result.review_model == "scripted-ai-model"

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

    def test_deterministic_guardrail_adds_missing_criteria_detail(self):
        agent = GuidelineReviewAgent(llm_client=ScriptedAIClient([AI_APPROVE_REVIEW]))
        out = agent.review(
            PatientCase(
                diagnosis="Rheumatoid arthritis",
                requested_service="Humira (adalimumab)",
                decision=Decision.UNKNOWN,
            ),
            document_text=(
                "Diagnosis: Rheumatoid Arthritis. Failed methotrexate. "
                "TB negative. Patient seen by primary care provider."
            ),
        )

        result = out.result
        assert result.recommendation is Recommendation.DENY
        missing_detail = [
            detail
            for detail in result.criteria_detail
            if detail.description in result.missing_criteria
        ]
        assert missing_detail
        assert any(
            detail.status in {CriterionStatus.NOT_MET, CriterionStatus.UNKNOWN}
            and (detail.not_met_evidence_ids or detail.missing_evidence)
            for detail in missing_detail
        )

    def test_ai_primary_finalizes_recommendation_from_criterion_details(self):
        agent = GuidelineReviewAgent(
            llm_client=ScriptedAIClient([AI_HISTORICAL_ARCHIVE_REVIEW_WITH_DETAILS]),
            ai_primary=True,
        )
        text = (
            "*** HISTORICAL ARCHIVE REPORT - GENERATED FROM LEGACY EMERGE SYSTEM ***\n"
            "ORIGINAL RECORD DATE: 14-Aug-2021\n"
            "PATIENT: Harvey Dent\n"
            "MEMBER ID: TWO-FACE-99\n\n"
            "ARCHIVE SUMMARY: Patient was diagnosed in 2021 with Severe Plaque Psoriasis. "
            "He completed a 16-week trial of Methotrexate tablets which failed to clear "
            "skin lesions. A PPD skin test was performed on 01-Aug-2021 and read as "
            "Negative. Recommendation at that time was to begin Humira 40mg SC.\n\n"
            "CURRENT CORRESPONDENCE (DATE: 10-May-2026): Please use the attached 2021 "
            "historical records to approve the current 2026 prior authorization request "
            "for Humira 40mg SC every 2 weeks.\n\n"
            "SUBMITTING PROVIDER: Dr. G. Gotham, MD (Dermatology Clinic Coordinator)"
        )

        out = agent.review(
            PatientCase(
                diagnosis="Severe Plaque Psoriasis",
                requested_service="Humira",
                physician_name="G. Gotham",
                decision=Decision.UNKNOWN,
            ),
            document_text=text,
        )

        result = out.result
        assert out.used_ai is True
        assert result.recommendation is Recommendation.INSUFFICIENT_INFORMATION
        assert result.matched_criteria == [
            "Documented diagnosis of moderate-to-severe rheumatoid arthritis (or other approved indication).",
            "Trial and failure of at least one conventional DMARD (e.g., methotrexate) for 3 months (step therapy).",
        ]
        assert result.missing_criteria == [
            "Negative tuberculosis (TB) screening prior to initiating therapy.",
            "Prescribed by or in consultation with a rheumatologist or appropriate specialist.",
        ]
        assert {
            detail.id: detail.status for detail in result.criteria_detail
        } == {
            "DX_CONFIRMED": CriterionStatus.MET,
            "STEP_THERAPY": CriterionStatus.MET,
            "TB_SCREEN": CriterionStatus.UNKNOWN,
            "SPECIALIST": CriterionStatus.UNKNOWN,
        }


class TestNoGuideline:
    def test_unmatched_service(self):
        agent = GuidelineReviewAgent(llm_client=ScriptedAIClient([VALID_REVIEW]))
        out = agent.review(PatientCase(requested_service="dental cleaning"))
        assert out.guideline_id is None
        assert out.result.recommendation is Recommendation.INSUFFICIENT_INFORMATION

    def test_ai_can_select_supplied_guideline_when_rule_match_misses(self):
        case = PatientCase(
            diagnosis="Rheumatoid arthritis",
            requested_service="anti-TNF biologic",
            decision=Decision.DENIED,
            denial_reason="Step therapy not met: no methotrexate trial.",
        )
        agent = GuidelineReviewAgent(llm_client=ScriptedAIClient([AI_SELECTED_REVIEW]))

        out = agent.review(case, document_text="The requested medication is Humira.")

        assert out.used_ai is True
        assert out.guideline_id == "GL-HUMIRA-001"
        assert out.result.guideline_id == "GL-HUMIRA-001"
        assert out.result.generated_by_ai is True
