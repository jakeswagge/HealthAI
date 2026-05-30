"""Evaluation framework for the clinical review engine / agent.

Measures across a set of labeled scenarios:
- Recommendation accuracy (predicted vs. expected recommendation)
- Schema compliance (output is a valid ReviewResult)
- JSON validity (a result object was produced without error)
- Guideline matching accuracy (correct guideline selected, when expected)

Backend-agnostic: pass any object with a ``review(case, document_text)`` method
that returns either a :class:`ReviewResult` or a :class:`ReviewAgentResult`.
Defaults to the deterministic :class:`ClinicalReviewEngine`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.patient_case import PatientCase
from app.models.review_result import Recommendation, ReviewResult
from app.review.engine import ClinicalReviewEngine


@dataclass
class ScenarioEvaluation:
    """Evaluation of a single review scenario."""

    name: str
    expected: str
    predicted: str | None
    json_valid: bool
    schema_compliant: bool
    guideline_ok: bool
    expected_guideline: str | None = None
    matched_guideline: str | None = None
    result: ReviewResult | None = None
    error: str | None = None

    @property
    def recommendation_correct(self) -> bool:
        return self.predicted == self.expected


@dataclass
class ReviewEvaluationReport:
    """Aggregate review evaluation."""

    scenarios: list[ScenarioEvaluation] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.scenarios)

    @property
    def recommendation_accuracy(self) -> float:
        if not self.scenarios:
            return 0.0
        ok = sum(s.recommendation_correct for s in self.scenarios)
        return round(ok / len(self.scenarios), 4)

    @property
    def json_validity_rate(self) -> float:
        if not self.scenarios:
            return 0.0
        return round(sum(s.json_valid for s in self.scenarios) / len(self.scenarios), 4)

    @property
    def schema_compliance_rate(self) -> float:
        if not self.scenarios:
            return 0.0
        return round(
            sum(s.schema_compliant for s in self.scenarios) / len(self.scenarios), 4
        )

    @property
    def guideline_matching_accuracy(self) -> float:
        relevant = [s for s in self.scenarios if s.expected_guideline]
        if not relevant:
            return 1.0
        return round(sum(s.guideline_ok for s in relevant) / len(relevant), 4)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "recommendation_accuracy": self.recommendation_accuracy,
            "json_validity_rate": self.json_validity_rate,
            "schema_compliance_rate": self.schema_compliance_rate,
            "guideline_matching_accuracy": self.guideline_matching_accuracy,
        }


def _coerce_to_review_result(obj) -> tuple[ReviewResult | None, str | None]:
    """Normalize an engine/agent return value into a ReviewResult."""
    if isinstance(obj, ReviewResult):
        return obj, getattr(obj, "guideline_id", None)
    # ReviewAgentResult-like
    result = getattr(obj, "result", None)
    if isinstance(result, ReviewResult):
        return result, getattr(obj, "guideline_id", None) or result.guideline_id
    return None, None


def evaluate_scenario(scenario: dict, reviewer) -> ScenarioEvaluation:
    """Evaluate a single labeled scenario with the given reviewer."""
    name = scenario["name"]
    expected = scenario["expected"]
    expected_guideline = scenario.get("expected_guideline")
    case = scenario["case"]
    document_text = scenario.get("document_text")

    ev = ScenarioEvaluation(
        name=name,
        expected=expected,
        predicted=None,
        json_valid=False,
        schema_compliant=False,
        guideline_ok=(expected_guideline is None),
        expected_guideline=expected_guideline,
    )

    try:
        raw = reviewer.review(case, document_text)
        result, guideline_id = _coerce_to_review_result(raw)
        if result is None:
            ev.error = "reviewer did not return a ReviewResult"
            return ev
        ev.json_valid = True
        ev.schema_compliant = isinstance(result, ReviewResult)
        ev.predicted = result.recommendation.value
        ev.result = result
        ev.matched_guideline = guideline_id
        if expected_guideline:
            ev.guideline_ok = guideline_id == expected_guideline
    except Exception as exc:  # noqa: BLE001 - record and continue
        ev.error = f"{type(exc).__name__}: {exc}"

    return ev


def run_review_evaluation(
    scenarios: list[dict],
    reviewer=None,
) -> ReviewEvaluationReport:
    """Run review evaluation over labeled scenarios."""
    reviewer = reviewer or ClinicalReviewEngine()
    report = ReviewEvaluationReport()
    for scenario in scenarios:
        report.scenarios.append(evaluate_scenario(scenario, reviewer))
    return report
