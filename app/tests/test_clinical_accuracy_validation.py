"""Tests for clinical-review auto-decision accuracy gates."""

from __future__ import annotations

from app.models.review_result import CriterionEvaluation, CriterionStatus, Recommendation, ReviewResult
from app.validation.clinical_accuracy import (
    AutoDecisionPolicy,
    auto_decision_reasons,
    evaluate_clinical_gold_set,
)
from validation.run import main


class Reviewer:
    def __init__(self, review: ReviewResult):
        self._review = review

    def review(self, case, document_text=None):
        return self._review


def _traceable_review(
    recommendation: Recommendation = Recommendation.APPROVE,
    *,
    confidence: float = 1.0,
) -> ReviewResult:
    return ReviewResult(
        recommendation=recommendation,
        matched_criteria=["Criterion"],
        rationale="Traceable review.",
        confidence_score=confidence,
        guideline_id="GL-HUMIRA-001",
        criteria_detail=[
            CriterionEvaluation(
                id="criterion",
                description="Criterion",
                met=True,
                status=CriterionStatus.MET,
                supporting_evidence_ids=["EV-1"],
                confidence_score=confidence,
            )
        ],
    )


def test_auto_decision_requires_confidence_and_traceability():
    low_confidence = _traceable_review(confidence=0.95)
    untraceable = _traceable_review()
    untraceable.criteria_detail[0].supporting_evidence_ids = []

    assert auto_decision_reasons(
        low_confidence,
        AutoDecisionPolicy(min_confidence=0.99),
    )
    assert any(
        "traceability" in reason.lower()
        for reason in auto_decision_reasons(untraceable)
    )


def test_clinical_accuracy_counts_auto_decided_false_approve():
    report = evaluate_clinical_gold_set(
        [{"case_id": "G1", "expected": "DENY", "case": object(), "slices": ["tb"]}],
        reviewer=Reviewer(_traceable_review(Recommendation.APPROVE)),
        policy=AutoDecisionPolicy(min_confidence=0.99),
    )

    assert report.auto_decided_total == 1
    assert report.auto_decided_accuracy == 0.0
    assert report.false_approve_count == 1
    assert report.safety_slice_failures == {"tb": 1}
    assert not report.passes_release_gate


def test_clinical_accuracy_cli_runs_seed_scorecard(capsys):
    exit_code = main(
        [
            "clinical-accuracy",
            "--seed",
            "--allow-untraceable",
            "--auto-threshold",
            "0.0",
        ]
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Clinical auto-decision scorecard" in out
    assert "Release gate:" in out
