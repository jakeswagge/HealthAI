"""Regression tests for appeal wording alignment with clinical review state."""

from __future__ import annotations

from app.appeals.appeal_agent import AppealGenerationAgent
from app.appeals.builder import AppealLetterBuilder
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.review.engine import ClinicalReviewEngine
from app.services.mock_claude_client import MockClaudeClient, MockScenario


AFFIRMATIVE_ASSERTIONS = [
    "supports medical necessity",
    "medical necessity supported",
    "meets criteria",
    "meets guideline criteria",
    "criteria satisfied",
    "satisfies guideline requirements",
    "medically necessary",
    "approval recommended",
    "approval justified",
]


HUMIRA_DENIAL = PatientCase(
    patient_name="Harold T. Greene",
    member_id="WP-558210334",
    date_of_birth="04/17/1971",
    diagnosis="Moderate to severe rheumatoid arthritis",
    icd10_codes=["M06.9"],
    requested_service="Humira (adalimumab)",
    cpt_codes=["J0135"],
    insurance_company="WellPoint National Insurance",
    decision=Decision.DENIED,
    denial_reason=(
        "Step therapy requirement not met: no documented trial of a "
        "conventional DMARD (methotrexate)."
    ),
    physician_name="Dr. Susan A. Patel, MD",
)

HUMIRA_COMPLETE_DENIAL_CASE = PatientCase(
    patient_name="Maria Complete",
    member_id="WP-100200300",
    diagnosis="Moderate to severe rheumatoid arthritis",
    icd10_codes=["M06.9"],
    requested_service="Humira (adalimumab)",
    cpt_codes=["J0135"],
    insurance_company="WellPoint National Insurance",
    decision=Decision.DENIED,
    denial_reason="Coverage denied pending appeal review.",
    physician_name="Dr. Susan A. Patel, MD",
)

HUMIRA_COMPLETE_DOC = (
    "Moderate to severe rheumatoid arthritis. Failed methotrexate (DMARD) "
    "for 3 months. Negative TB screen. Rheumatologist prescribing."
)


def _review(case: PatientCase, document_text: str | None = None) -> ReviewResult:
    return ClinicalReviewEngine().review(case, document_text)


def _assert_no_affirmative_language(text: str) -> None:
    lower = text.lower()
    for phrase in AFFIRMATIVE_ASSERTIONS:
        assert phrase not in lower


def test_humira_deny_with_missing_criteria_generates_deficiency_appeal():
    review = _review(HUMIRA_DENIAL)
    assert review.recommendation is Recommendation.DENY
    assert len(review.missing_criteria) == 3

    appeal = AppealLetterBuilder().build(HUMIRA_DENIAL, review)
    text = appeal.letter_text
    lower = text.lower()

    assert "documentation deficiency appeal" in lower
    assert "the denial appears related to missing or undocumented criteria" in lower
    assert "additional documentation required" in lower
    assert (
        "additional clinical documentation is required before reconsideration "
        "can be expected"
    ) in lower
    for criterion in review.missing_criteria:
        assert criterion in text
    _assert_no_affirmative_language(text)


def test_humira_approve_with_all_criteria_may_assert_medical_necessity():
    review = _review(HUMIRA_COMPLETE_DENIAL_CASE, HUMIRA_COMPLETE_DOC)
    assert review.recommendation is Recommendation.APPROVE
    assert not review.missing_criteria

    appeal = AppealLetterBuilder().build(HUMIRA_COMPLETE_DENIAL_CASE, review)

    assert "supports medical necessity" in appeal.letter_text.lower()


def test_insufficient_information_outputs_incomplete_package_only():
    case = PatientCase(
        patient_name="Sarah Johnson",
        requested_service="Humira (adalimumab)",
        decision=Decision.DENIED,
        denial_reason="Clinical documentation incomplete.",
    )
    review = ReviewResult(
        recommendation=Recommendation.INSUFFICIENT_INFORMATION,
        missing_criteria=["TB screening"],
        missing_evidence=["TB screening result"],
        rationale="Insufficient information to determine clinical coverage.",
    )

    appeal = AppealLetterBuilder().build(case, review)
    lower = appeal.letter_text.lower()

    assert "incomplete case package" in lower
    assert "cannot generate a complete appeal" in lower
    _assert_no_affirmative_language(appeal.letter_text)


def test_ai_agent_cannot_override_deny_missing_criteria_safety():
    unsafe_payload = {
        "appeal_reason": "The information supports medical necessity.",
        "clinical_summary": "Rheumatoid arthritis.",
        "guideline_support": ["The criteria are satisfied."],
        "missing_information": [],
        "recommended_next_steps": ["Approval recommended."],
        "confidence_score": 0.95,
        "letter_text": (
            "## Request For Reconsideration\n"
            "Medical necessity supported; approval justified."
        ),
    }
    review = _review(HUMIRA_DENIAL)
    agent = AppealGenerationAgent(
        llm_client=MockClaudeClient(MockScenario.VALID, base_case=unsafe_payload)
    )

    out = agent.generate(HUMIRA_DENIAL, review)

    assert out.used_ai is False
    assert out.attempts == 0
    for criterion in review.missing_criteria:
        assert criterion in out.appeal.letter_text
    _assert_no_affirmative_language(out.appeal.letter_text)
