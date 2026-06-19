from validation.clinical_fact_validation import (
    clinical_fact_coverage_report,
    conflict_validation_report,
    traceability_validation_report,
)


def test_validation_conflict_scenarios_route_to_human_review():
    report = conflict_validation_report()

    scenarios = {row["scenario"]: row for row in report["scenarios"]}
    assert set(scenarios) == {
        "TB Positive + TB Negative",
        "MTX Failed + MTX Refused",
        "RA + Lupus",
        "Specialist + PCP",
    }

    for row in scenarios.values():
        assert row["routing"] == "HUMAN_REVIEW"
        assert row["conflict_detected"] is True
        assert row["requires_human_review"] is True
        assert row["review_recommendation_after_gate"] == "HUMAN_REVIEW"
        assert row["raw_review_recommendation"] == "SKIPPED_DUE_TO_CONFLICT"
        assert row["conflicts"]

    assert scenarios["TB Positive + TB Negative"]["conflicts"][0]["fact_type"] == (
        "tb_screen_result"
    )
    assert scenarios["MTX Failed + MTX Refused"]["conflicts"][0]["fact_type"] == (
        "step_therapy_status"
    )
    assert scenarios["RA + Lupus"]["conflicts"][0]["fact_type"] == "diagnosis"
    assert scenarios["Specialist + PCP"]["conflicts"][0]["fact_type"] == (
        "provider_role"
    )


def test_validation_traceability_has_supporting_and_not_met_evidence():
    report = traceability_validation_report()
    criteria = {row["criterion_id"]: row for row in report["criteria"]}

    assert report["recommendation"] == "DENY"
    assert report["contraindications_found"] == [
        "Positive tuberculosis (TB) evidence detected."
    ]

    assert criteria["DX_CONFIRMED"]["supporting_evidence_ids"]
    assert criteria["STEP_THERAPY"]["supporting_evidence_ids"]
    assert criteria["SPECIALIST"]["supporting_evidence_ids"]

    tb = criteria["TB_SCREEN"]
    assert tb["status"] == "not_met"
    assert tb["supporting_evidence_ids"]
    assert tb["not_met_evidence_ids"]
    assert tb["supporting_evidence_ids"] == tb["not_met_evidence_ids"]
    assert tb["not_met_evidence"][0]["source_document"] == "tb-lab.txt"
    assert "POSITIVE" in tb["not_met_evidence"][0]["quoted_text"]


def test_validation_clinical_fact_coverage_has_no_humira_legacy_fallbacks():
    report = clinical_fact_coverage_report()

    assert report["remaining_legacy_string_logic"] == []
    criteria = {row["criterion_id"]: row for row in report["criteria"]}
    assert set(criteria) == {
        "DX_CONFIRMED",
        "STEP_THERAPY",
        "TB_SCREEN",
        "SPECIALIST",
    }
    for row in criteria.values():
        assert row["consumes_clinical_fact"] is True
        assert row["legacy_string_fallback_used"] is False
        assert row["clinical_fact_ids"]
        assert row["clinical_fact_states"]
