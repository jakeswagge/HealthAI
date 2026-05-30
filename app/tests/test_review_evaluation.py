"""Review-evaluation tests over the labeled scenario set.

Asserts recommendation accuracy, schema compliance, JSON validity, and
guideline-matching accuracy meet thresholds using the deterministic engine and
the offline review agent.
"""

from __future__ import annotations

import pytest

from app.review.engine import ClinicalReviewEngine
from app.review.evaluation import run_review_evaluation
from app.review.review_agent import GuidelineReviewAgent
from app.services.local_client import LocalHeuristicClient
from app.tests.review_scenarios import (
    ALL_SCENARIOS,
    APPROVAL_SCENARIOS,
    DENIAL_SCENARIOS,
    INSUFFICIENT_SCENARIOS,
)


class TestScenarioCounts:
    def test_minimum_counts(self):
        assert len(APPROVAL_SCENARIOS) >= 10
        assert len(DENIAL_SCENARIOS) >= 10
        assert len(INSUFFICIENT_SCENARIOS) >= 5


@pytest.fixture(scope="module")
def engine_report():
    return run_review_evaluation(ALL_SCENARIOS, reviewer=ClinicalReviewEngine())


@pytest.fixture(scope="module")
def agent_report():
    agent = GuidelineReviewAgent(llm_client=LocalHeuristicClient())
    return run_review_evaluation(ALL_SCENARIOS, reviewer=agent)


class TestEngineMetrics:
    def test_all_json_valid(self, engine_report):
        assert engine_report.json_validity_rate == 1.0

    def test_all_schema_compliant(self, engine_report):
        assert engine_report.schema_compliance_rate == 1.0

    def test_guideline_matching_accuracy(self, engine_report):
        assert engine_report.guideline_matching_accuracy == 1.0, engine_report.as_dict()

    def test_recommendation_accuracy(self, engine_report):
        # Deterministic engine should fully agree with labels on this set.
        assert engine_report.recommendation_accuracy >= 0.95, [
            (s.name, s.expected, s.predicted)
            for s in engine_report.scenarios
            if not s.recommendation_correct
        ]


class TestAgentMetrics:
    def test_agent_matches_engine_offline(self, agent_report):
        assert agent_report.json_validity_rate == 1.0
        assert agent_report.schema_compliance_rate == 1.0
        assert agent_report.recommendation_accuracy >= 0.95, [
            (s.name, s.expected, s.predicted)
            for s in agent_report.scenarios
            if not s.recommendation_correct
        ]


class TestPerCategory:
    def test_all_approvals_correct(self, engine_report):
        for s in engine_report.scenarios:
            if s.expected == "APPROVE":
                assert s.predicted == "APPROVE", s.name

    def test_all_denials_correct(self, engine_report):
        for s in engine_report.scenarios:
            if s.expected == "DENY":
                assert s.predicted == "DENY", s.name

    def test_all_insufficient_correct(self, engine_report):
        for s in engine_report.scenarios:
            if s.expected == "INSUFFICIENT_INFORMATION":
                assert s.predicted == "INSUFFICIENT_INFORMATION", s.name
