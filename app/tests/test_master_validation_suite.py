from pathlib import Path

from app.models.governance import GovernanceSettings
from validation.run_validation_suite import run_suite


def test_master_validation_suite_generates_reports(tmp_path: Path):
    json_out = tmp_path / "MASTER_VALIDATION_RESULTS.json"
    md_out = tmp_path / "MASTER_VALIDATION_REPORT.md"

    report = run_suite(
        matrix_path=tmp_path / "missing_matrix.json",
        json_report_path=json_out,
        md_report_path=md_out,
        gemini_mode="skip",
        settings=GovernanceSettings(
            require_conflict_resolution=True,
            require_human_review_before_export=True,
            block_autonomous_denials=False,
            require_verified_appeal_claims=True,
        ),
    )

    assert json_out.exists()
    assert md_out.exists()
    assert report["metrics"]["total_cases"] == 7
    assert report["metrics"]["passed"] == 7
    assert report["metrics"]["safety_metrics"]["human_review_compliance"] == 1.0
    assert report["metrics"]["safety_metrics"]["traceability_success_rate"] == 1.0

    conflict_cases = {
        row["case_id"]: row
        for row in report["case_results"]
        if row["expected"].get("recommendation") == "HUMAN_REVIEW"
    }
    assert conflict_cases
    assert all(row["workflow_decision"] == "HUMAN_REVIEW" for row in conflict_cases.values())
    assert all(
        row["conflict_detection"]["requires_human_review"]
        for row in conflict_cases.values()
    )

    assert report["what_is_working"]["ClinicalFact"]
    assert report["what_is_working"]["Governance"]
    assert "## Executive Summary" in md_out.read_text(encoding="utf-8")
