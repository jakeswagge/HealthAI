"""Regression tests for MedSpaCy polarity-aware clinical review."""

from __future__ import annotations

from app.guidelines.repository import GuidelineRepository
from app.models.clinical_guideline import ClinicalGuideline, GuidelineCriterion
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation
from app.review.clinical_nlp import extract_clinical_signals, get_clinical_nlp
from app.review.engine import ClinicalReviewEngine


def _case() -> PatientCase:
    return PatientCase(
        diagnosis="Moderate to severe rheumatoid arthritis",
        requested_service="Humira (adalimumab)",
        decision=Decision.UNKNOWN,
    )


def _detail(result, criterion_id: str):
    return next(d for d in result.criteria_detail if d.id == criterion_id)


def _tb_only_engine() -> ClinicalReviewEngine:
    guideline = ClinicalGuideline(
        guideline_id="GL-TB-ONLY",
        service_name="Humira",
        diagnosis="Rheumatoid arthritis",
        version="test",
        source="test",
        aliases=["humira"],
        required_criteria=[
            GuidelineCriterion(
                id="TB_SCREEN",
                description="Negative tuberculosis (TB) screening prior to initiating therapy.",
                keywords=["tuberculosis", "tb screening", "negative tb"],
            )
        ],
    )
    return ClinicalReviewEngine(repository=GuidelineRepository([guideline]))


CASE_17_TEXT = """COVERAGE DECISION: DENIAL
Patient: Chandler Bing
Member ID: PAY017
Requested Service: Humira (adalimumab)

Explanation: Your provider requested Humira for Rheumatoid Arthritis. While we have received documentation of your methotrexate trial and specialist consultation, biologic medications suppress the immune system. Our clinical policy requires a negative Tuberculosis (TB) screening prior to starting this medication. This documentation was not provided."""

CASE_18_TEXT = """PRIOR AUTHORIZATION PENDING / DENIAL
Patient: Joey Tribbiani
Member ID: PAY018
Drug: Humira

Dear Member,
Your request for Humira has been denied due to a lack of clinical information. We received a request from your provider, but we did not receive the clinical chart notes detailing your specific diagnosis, past medication trials, or lab work (including TB screening). Without this information, we cannot determine if this medication meets medical necessity criteria."""

CASE_20_TEXT = """PHARMACY COVERAGE DENIAL
Patient: Janice Hosenstein
Member ID: PAY020
Requested: Humira

Reason for decision: We cannot approve your request for Humira. This medication is FDA-approved and covered under your plan for conditions such as Rheumatoid Arthritis, Psoriatic Arthritis, and Crohn's Disease. Your provider submitted a diagnosis of Osteoarthritis. Humira is not indicated or considered medically necessary for the treatment of Osteoarthritis."""


def test_medspacy_pipeline_loads_context_component():
    nlp = get_clinical_nlp()

    assert nlp is not None
    assert "medspacy_context" in nlp.pipe_names
    assert "medspacy_target_matcher" in nlp.pipe_names


def test_target_matcher_extracts_requested_targets_with_context():
    signals = extract_clinical_signals(
        "No signs of TB. Rheumatoid Arthritis treated with Methotrexate. "
        "Humira requested after Enbrel. Hepatitis B negative. Rheumatologist consulted."
    )
    labels = {s.label for s in signals}
    tb = next(s for s in signals if s.label == "TB")

    assert {
        "TB",
        "DIAGNOSIS_RA",
        "STEP_THERAPY",
        "BIOLOGIC_HUMIRA",
        "BIOLOGIC_ENBREL",
        "HEP_B",
        "SPECIALIST_RHEUM",
    }.issubset(labels)
    assert tb.is_negated is True


def test_quantiferon_tb_gold_positive_fails_tb_screen_and_denies():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Failed methotrexate. Rheumatologist prescribing. "
            "Quantiferon-TB Gold positive."
        ),
    )

    tb = _detail(result, "TB_SCREEN")
    assert tb.met is False
    assert "positive tb evidence" in (tb.note or "").lower()
    assert result.recommendation is Recommendation.DENY
    assert any("tuberculosis" in c.lower() for c in result.contraindications_found)


def test_positive_tb_evidence_wins_over_negative_tb_screening():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Failed methotrexate. TB negative. Rheumatologist prescribing. "
            "Separate lab: Quantiferon-TB Gold positive."
        ),
    )

    tb = _detail(result, "TB_SCREEN")
    assert tb.met is False
    assert "positive tb evidence" in (tb.note or "").lower()
    assert result.recommendation is Recommendation.DENY
    assert any("tuberculosis" in c.lower() for c in result.contraindications_found)


def test_tb_negative_satisfies_tb_screen():
    result = ClinicalReviewEngine().review(
        _case(),
        "Failed methotrexate. TB negative. Rheumatologist prescribing.",
    )

    tb = _detail(result, "TB_SCREEN")
    assert tb.met is True
    assert "negative tb" in (tb.note or "").lower()


def test_no_tuberculosis_screening_performed_does_not_satisfy_tb_screen():
    result = ClinicalReviewEngine().review(
        _case(),
        "Failed methotrexate. Rheumatologist prescribing. No tuberculosis screening performed.",
    )

    tb = _detail(result, "TB_SCREEN")
    assert tb.met is False
    assert "absence" in (tb.note or "").lower()


def test_policy_requirement_without_tb_documentation_does_not_satisfy_tb_screen():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Failed methotrexate. Rheumatologist prescribing. "
            "Our clinical policy requires a negative tuberculosis (tb) screening "
            "before approval. This documentation was not provided."
        ),
    )

    tb = _detail(result, "TB_SCREEN")
    assert tb.met is False
    assert "missing" in (tb.note or "").lower() or "not received" in (tb.note or "").lower()
    assert result.recommendation is Recommendation.DENY


def test_policy_requirement_text_alone_is_not_patient_tb_evidence():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Failed methotrexate. Rheumatologist prescribing. "
            "Clinical policy states the member must be tuberculosis screened "
            "prior to starting Humira."
        ),
    )

    tb = _detail(result, "TB_SCREEN")
    assert tb.met is False
    assert "negative tb screening evidence" not in (tb.note or "").lower()
    assert result.recommendation is not Recommendation.APPROVE


def test_exact_case_17_tb_screen_is_unmet_and_denies():
    result = ClinicalReviewEngine().review(
        PatientCase(
            diagnosis=None,
            requested_service="Humira",
            decision=Decision.DENIED,
        ),
        CASE_17_TEXT,
    )

    tb = _detail(result, "TB_SCREEN")
    assert tb.met is False
    assert tb.description in result.missing_criteria
    assert tb.description not in result.matched_criteria
    assert result.recommendation is Recommendation.DENY


def test_negated_tb_documentation_context_forces_tb_screen_unmet():
    result = _tb_only_engine().review(
        PatientCase(
            diagnosis="Rheumatoid arthritis",
            requested_service="Humira",
            decision=Decision.UNKNOWN,
        ),
        (
            "We did not receive tuberculosis (tb) screening documentation."
        ),
    )

    tb = _detail(result, "TB_SCREEN")
    assert tb.met is False
    assert result.matched_criteria == []
    assert result.missing_criteria == [tb.description]
    assert result.recommendation is Recommendation.DENY


def test_exact_case_18_negated_tb_screen_is_not_matched():
    result = ClinicalReviewEngine().review(
        PatientCase(
            diagnosis=None,
            requested_service="Humira",
            decision=Decision.DENIED,
        ),
        CASE_18_TEXT,
    )

    tb = _detail(result, "TB_SCREEN")
    assert tb.met is False
    assert tb.description not in result.matched_criteria
    assert tb.description in result.missing_criteria
    assert "context=negated" in (tb.note or "").lower()


def test_rule_out_ra_does_not_satisfy_diagnosis():
    case = PatientCase(
        diagnosis="Rule out RA",
        requested_service="Humira (adalimumab)",
        decision=Decision.UNKNOWN,
    )
    result = ClinicalReviewEngine().review(
        case,
        "Rule out RA. Failed methotrexate. TB negative. Rheumatologist prescribing.",
    )

    diagnosis = _detail(result, "DX_CONFIRMED")
    assert diagnosis.met is False
    assert result.recommendation is Recommendation.DENY


def test_conflicting_current_diagnoses_do_not_auto_approve():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Patient being treated for severe Psoriatic Arthritis. MTX failed. "
            "TB negative. Rheum consult complete. Diagnosis: Rheumatoid Arthritis. "
            "Requested: Humira."
        ),
    )

    diagnosis = _detail(result, "DX_CONFIRMED")
    assert diagnosis.met is False
    assert "conflicting current diagnoses" in (diagnosis.note or "").lower()
    assert result.recommendation is not Recommendation.APPROVE


def test_covered_condition_boilerplate_does_not_create_diagnosis_conflict():
    case = PatientCase(
        diagnosis="Osteoarthritis",
        requested_service="Humira",
        decision=Decision.UNKNOWN,
    )
    result = ClinicalReviewEngine().review(
        case,
        (
            "Diagnosis: Osteoarthritis. Failed methotrexate. TB negative. "
            "Rheumatologist prescribing. Humira is FDA-approved and covered "
            "under your plan for conditions such as Rheumatoid Arthritis and "
            "Psoriatic Arthritis."
        ),
    )

    diagnosis = _detail(result, "DX_CONFIRMED")
    assert diagnosis.met is False
    assert "conflicting current diagnoses" not in (diagnosis.note or "").lower()
    assert result.recommendation is not Recommendation.APPROVE


def test_exact_case_20_boilerplate_does_not_create_ra_psa_conflict():
    result = ClinicalReviewEngine().review(
        PatientCase(
            diagnosis="Osteoarthritis",
            requested_service="Humira",
            decision=Decision.DENIED,
        ),
        CASE_20_TEXT,
    )

    diagnosis = _detail(result, "DX_CONFIRMED")
    assert diagnosis.met is False
    assert "conflicting current diagnoses" not in (diagnosis.note or "").lower()
    assert "osteoarthritis" in (diagnosis.note or "").lower()
    assert result.recommendation is Recommendation.DENY


def test_no_methotrexate_trial_fails_step_therapy():
    result = ClinicalReviewEngine().review(
        _case(),
        "No methotrexate trial documented. TB negative. Rheumatologist prescribing.",
    )

    step = _detail(result, "STEP_THERAPY")
    assert step.met is False
    assert "absence" in (step.note or "").lower()


def test_refused_methotrexate_fails_step_therapy():
    result = ClinicalReviewEngine().review(
        _case(),
        "Patient refused methotrexate. TB negative. Rheumatologist prescribing.",
    )

    step = _detail(result, "STEP_THERAPY")
    assert step.met is False
    assert "refusal" in (step.note or "").lower()
    assert result.recommendation is Recommendation.DENY


def test_denial_context_missing_rheumatology_consult_remains_unmet():
    case = _case()
    case.denial_reason = "Denied because rheumatology consult documentation is missing."
    result = ClinicalReviewEngine().review(
        case,
        "Failed methotrexate. TB negative.",
    )

    specialist = _detail(result, "SPECIALIST")
    assert specialist.met is False
    assert result.recommendation is Recommendation.DENY


def test_bare_methotrexate_mention_does_not_satisfy_step_therapy():
    result = ClinicalReviewEngine().review(
        _case(),
        "Methotrexate appears on the medication list. TB negative. Rheumatologist prescribing.",
    )

    step = _detail(result, "STEP_THERAPY")
    assert step.met is False
    assert result.recommendation is Recommendation.INSUFFICIENT_INFORMATION


def test_current_humira_and_enbrel_flags_duplicate_biologic():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Failed methotrexate. TB negative. Rheumatologist prescribing. "
            "Currently on Enbrel and Humira requested."
        ),
    )

    assert result.recommendation is Recommendation.DENY
    assert any("concurrent biologic" in c.lower() for c in result.contraindications_found)


def test_prior_failed_enbrel_does_not_flag_duplicate_biologic():
    result = ClinicalReviewEngine().review(
        _case(),
        (
            "Failed methotrexate. TB negative. Rheumatologist prescribing. "
            "Previously failed Enbrel. Humira requested."
        ),
    )

    assert not any("concurrent biologic" in c.lower() for c in result.contraindications_found)
