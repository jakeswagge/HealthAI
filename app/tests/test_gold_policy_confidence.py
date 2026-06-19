"""Tests for adjudicated gold sets, formulary policy, and calibrated confidence."""

from __future__ import annotations

import json

from app.importers.davinci_formulary import FormularyCatalog, FormularyItem
from app.cases.service import CaseService
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import CriterionEvaluation, CriterionStatus, Recommendation, ReviewResult
from app.policies.formulary import FormularyPolicyIndex
from app.review.engine import ClinicalReviewEngine
from app.storage.database import connect, initialize_schema
from app.validation.clinical_accuracy import (
    AutoDecisionPolicy,
    CalibrationBucket,
    ConfidenceCalibration,
    build_confidence_calibration,
    calibrated_confidence,
    evaluate_clinical_gold_set,
)
from app.validation.gold_set import load_adjudicated_clinical_gold_set


def _detail(result, criterion_id: str):
    return next(d for d in result.criteria_detail if d.id == criterion_id)


def test_loads_human_adjudicated_gold_set(tmp_path):
    path = tmp_path / "gold.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "GOLD-1",
                        "expected": "APPROVE",
                        "reviewer": "reviewer-a",
                        "reviewer_rationale": "All criteria met.",
                        "locked_holdout": True,
                        "documents": [
                            {
                                "filename": "note.txt",
                                "text": (
                                    "Diagnosis: Rheumatoid Arthritis\n"
                                    "Requested Service: Humira\n"
                                    "Failed methotrexate. TB negative. "
                                    "Rheumatologist prescribing."
                                ),
                            }
                        ],
                        "criteria": [
                            {
                                "criterion_id": "STEP_THERAPY",
                                "status": "MET",
                                "evidence_ids": ["EV-1"],
                                "rationale": "MTX failed.",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    scenarios = load_adjudicated_clinical_gold_set(path)

    assert len(scenarios) == 1
    assert scenarios[0]["expected"] == "APPROVE"
    assert scenarios[0]["adjudication"]["reviewer"] == "reviewer-a"
    assert scenarios[0]["adjudication"]["criteria"][0]["criterion_id"] == "STEP_THERAPY"
    assert scenarios[0]["case"].requested_service == "Humira"


def test_formulary_policy_can_waive_step_therapy_without_waiving_safety_criteria():
    catalog = FormularyCatalog(
        items=[
            FormularyItem(
                item_id="Item-Humira",
                drug_reference="Humira",
                prior_authorization_required=True,
                step_therapy_required=False,
            )
        ]
    )
    policy = FormularyPolicyIndex.from_catalog(catalog)
    case = PatientCase(
        diagnosis="Moderate to severe rheumatoid arthritis",
        requested_service="Humira",
        decision=Decision.UNKNOWN,
    )
    result = ClinicalReviewEngine(formulary_policy=policy).review(
        case,
        "Moderate to severe rheumatoid arthritis. TB negative. Rheumatologist prescribing.",
    )

    step = _detail(result, "STEP_THERAPY")
    tb = _detail(result, "TB_SCREEN")

    assert step.status is CriterionStatus.MET
    assert "does not require step therapy" in (step.note or "").lower()
    assert tb.status is CriterionStatus.MET
    assert result.recommendation is Recommendation.APPROVE
    assert result.safety_gate["policy_rules"]["step_therapy_required"] is False


def test_formulary_policy_matches_davinci_brand_display_alias():
    policy = FormularyPolicyIndex.from_items(
        [
            {
                "item_id": "Item-1",
                "drug_reference": "1 ML adalimumab 40 MG/0.4ML Auto-Injector [Humira]",
                "prior_authorization_required": True,
                "step_therapy_required": False,
            }
        ]
    )

    assert policy.rule_for("Humira") is not None
    assert policy.step_therapy_required_for("adalimumab") is False


def test_payer_review_flow_uses_formulary_policy():
    conn = connect(":memory:")
    try:
        initialize_schema(conn)
        catalog = FormularyCatalog(
            items=[
                FormularyItem(
                    item_id="Item-Humira",
                    drug_reference="Humira",
                    prior_authorization_required=True,
                    step_therapy_required=False,
                )
            ]
        )
        service = CaseService(
            conn=conn,
            formulary_policy=FormularyPolicyIndex.from_catalog(catalog),
        )
        rec = service.create_case("payer formulary case")
        service.ingest_document(
            rec.case_id,
            "note.txt",
            (
                "Diagnosis: Moderate to severe rheumatoid arthritis\n"
                "Requested Service: Humira\n"
                "TB negative. Rheumatologist prescribing."
            ).encode("utf-8"),
        )
        service.assemble_case(rec.case_id)

        review = service.review_with_payer(rec.case_id, "DEFAULT").review
        step = _detail(review, "STEP_THERAPY")

        assert review.recommendation is Recommendation.APPROVE
        assert step.status is CriterionStatus.MET
        assert review.safety_gate["policy_rules"]["step_therapy_required"] is False
    finally:
        conn.close()


def test_calibrated_confidence_penalizes_untraceable_met_criteria():
    result = ClinicalReviewEngine().review(
        PatientCase(
            diagnosis="Moderate to severe rheumatoid arthritis",
            requested_service="Humira",
            decision=Decision.UNKNOWN,
        ),
        "Moderate to severe rheumatoid arthritis. Failed methotrexate. TB negative. Rheumatologist prescribing.",
    )

    strict = calibrated_confidence(result, AutoDecisionPolicy(require_traceability=True))
    relaxed = calibrated_confidence(result, AutoDecisionPolicy(require_traceability=False))

    assert strict < relaxed
    assert 0.0 <= strict <= 1.0


def test_calibration_caps_confidence_for_false_approve_bucket():
    result = ReviewResult(
        recommendation=Recommendation.APPROVE,
        matched_criteria=["Criterion"],
        rationale="Traceable approval.",
        confidence_score=1.0,
        criteria_detail=[
            CriterionEvaluation(
                id="criterion",
                description="Criterion",
                met=True,
                status=CriterionStatus.MET,
                supporting_evidence_ids=["EV-1"],
                confidence_score=1.0,
            )
        ],
    )
    calibration = ConfidenceCalibration(
        overall=CalibrationBucket(total=10, correct=9, false_approves=1),
        by_recommendation={
            Recommendation.APPROVE.value: CalibrationBucket(
                total=5,
                correct=4,
                false_approves=1,
            )
        },
        by_slice={"tb": CalibrationBucket(total=5, correct=4, false_approves=1)},
        min_examples=5,
    )

    confidence = calibrated_confidence(
        result,
        AutoDecisionPolicy(require_traceability=False, calibration=calibration),
        slices=["tb"],
    )

    assert confidence <= calibration.false_approve_cap


def test_adjudicated_criterion_labels_are_scored(tmp_path):
    path = tmp_path / "gold.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "GOLD-CRITERIA",
                        "expected": "APPROVE",
                        "documents": [
                            {
                                "filename": "note.txt",
                                "text": (
                                    "Diagnosis: Rheumatoid Arthritis\n"
                                    "Requested Service: Humira\n"
                                    "Failed methotrexate. TB negative. "
                                    "Rheumatologist prescribing."
                                ),
                            }
                            ],
                            "criteria": [
                                {"criterion_id": "DX_CONFIRMED", "status": "MET"},
                                {"criterion_id": "STEP_THERAPY", "status": "MET"},
                            ],
                        }
                    ]
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_clinical_gold_set(
        load_adjudicated_clinical_gold_set(path),
        policy=AutoDecisionPolicy(min_confidence=0.0, require_traceability=False),
    )

    assert report.criterion_total == 2
    assert report.criterion_correct == 2
    assert report.criterion_accuracy == 1.0


def test_build_confidence_calibration_from_adjudicated_outcomes(tmp_path):
    path = tmp_path / "gold.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "GOLD-CAL",
                        "expected": "APPROVE",
                        "slices": ["tb"],
                        "documents": [
                            {
                                "filename": "note.txt",
                                "text": (
                                    "Diagnosis: Rheumatoid Arthritis\n"
                                    "Requested Service: Humira\n"
                                    "Failed methotrexate. TB negative. "
                                    "Rheumatologist prescribing."
                                ),
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    calibration = build_confidence_calibration(
        load_adjudicated_clinical_gold_set(path),
        min_examples=1,
    )

    assert calibration.overall.total == 1
    assert calibration.by_recommendation[Recommendation.APPROVE.value].accuracy == 1.0
    assert calibration.by_slice["tb"].total == 1
