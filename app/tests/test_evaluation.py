"""Evaluation-framework tests over the sample corpus (local backend).

These tests measure extraction quality and assert minimum thresholds so the
suite fails if extraction quality regresses.
"""

from __future__ import annotations

import pytest

from app.agents.evaluation import run_evaluation
from app.agents.medical_extraction_agent import MedicalExtractionAgent
from app.services.local_client import LocalHeuristicClient
from app.tests.ground_truth import (
    APPROVAL_FILES,
    DENIAL_FILES,
    GROUND_TRUTH,
)


@pytest.fixture(scope="module")
def report():
    agent = MedicalExtractionAgent(LocalHeuristicClient())
    return run_evaluation(GROUND_TRUTH, agent=agent)


class TestCorpus:
    def test_corpus_size(self):
        assert len(APPROVAL_FILES) == 5
        assert len(DENIAL_FILES) == 5
        assert len(GROUND_TRUTH) == 10


class TestAggregateMetrics:
    def test_all_json_valid(self, report):
        assert report.json_validity_rate == 1.0

    def test_all_schema_compliant(self, report):
        assert report.schema_compliance_rate == 1.0

    def test_missing_field_handling(self, report):
        # Every doc must correctly leave known-absent fields empty.
        assert report.missing_field_handling_rate == 1.0

    def test_field_accuracy_threshold(self, report):
        # Local heuristic backend should clear a high bar on these clean docs.
        assert report.field_accuracy >= 0.95, report.as_dict()


class TestPerDocument:
    def test_no_extraction_failures(self, report):
        failed = [d.filename for d in report.docs if not d.json_valid]
        assert failed == [], f"Docs failed extraction: {failed}"

    def test_decisions_correct(self, report):
        for d in report.docs:
            assert d.case is not None
            expected = GROUND_TRUTH[d.filename]["decision"]
            assert d.case.decision.value == expected, d.filename

    def test_denials_have_reason(self, report):
        for d in report.docs:
            if d.filename.startswith("denial"):
                assert d.case.denial_reason, f"{d.filename} missing denial reason"

    def test_approvals_have_no_denial_reason(self, report):
        for d in report.docs:
            if d.filename.startswith("approval"):
                assert d.case.denial_reason is None, d.filename

    def test_confidence_in_range(self, report):
        for d in report.docs:
            assert 0.0 <= d.case.confidence_score <= 1.0
