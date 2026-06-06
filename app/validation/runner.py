"""ValidationRunner: exercise the full pipeline against mock scenarios.

For each scenario the runner:
1. creates a fresh case (in-memory DB),
2. ingests the scenario documents,
3. assembles + scores evidence,
4. runs a governance-enforced, payer-pack-aware review per payer,
5. checks the scenario expectations.

It produces a :class:`ValidationReport` summarizing pass/fail per (scenario,
payer). Fully offline and deterministic (uses the local heuristic backend);
no PHI and no proprietary payer content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.cases.service import CaseService
from app.models.governance import GovernanceSettings
from app.storage.database import connect, initialize_schema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = PROJECT_ROOT / "validation" / "datasets" / "scenarios.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ValidationResult:
    """Outcome of one (scenario, payer) check."""

    scenario_id: str
    payer_id: str
    passed: bool
    expected: str
    actual: str
    guideline_pack: str = ""
    guideline_version: str = ""
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "payer_id": self.payer_id,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "guideline_pack": self.guideline_pack,
            "guideline_version": self.guideline_version,
            "notes": self.notes,
        }


@dataclass
class ValidationReport:
    """Aggregate validation outcome."""

    generated_at: str = field(default_factory=_utc_now_iso)
    results: list[ValidationResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.total > 0

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.pass_rate,
            "all_passed": self.all_passed,
            "results": [r.as_dict() for r in self.results],
        }


def load_default_scenarios(path: str | Path = DEFAULT_DATASET) -> list[dict]:
    """Load scenario dicts from the dataset JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("scenarios", [])


class ValidationRunner:
    """Run validation scenarios against a CaseService."""

    def __init__(
        self,
        service: CaseService | None = None,
        settings: GovernanceSettings | None = None,
    ) -> None:
        # Default to an isolated in-memory service so the runner never touches
        # the production database.
        if service is None:
            conn = connect(":memory:")
            initialize_schema(conn)
            service = CaseService(conn=conn)
        self.service = service
        # Draft mode by default so the validation focuses on guideline-pack
        # behavior (governance enforcement is covered by its own tests).
        self.settings = settings or GovernanceSettings()

    # ------------------------------------------------------------------ #
    # Scenario execution
    # ------------------------------------------------------------------ #
    def _prepare_case(self, scenario: dict) -> str:
        rec = self.service.create_case(scenario.get("scenario_id", "validation"))
        case_id = rec.case_id
        for doc in scenario.get("documents", []):
            self.service.ingest_document(
                case_id, doc["filename"], doc["text"].encode("utf-8")
            )
        self.service.assemble_case(case_id)
        self.service.score_evidence(case_id)
        return case_id

    @staticmethod
    def _check(expected: dict, actual_rec: str) -> tuple[bool, str]:
        """Return (passed, expected_label) for a single payer expectation."""
        if "recommendation" in expected:
            exp = str(expected["recommendation"]).upper()
            return actual_rec == exp, exp
        if "recommendation_in" in expected:
            allowed = [str(x).upper() for x in expected["recommendation_in"]]
            return actual_rec in allowed, " or ".join(allowed)
        # No explicit expectation: pass through (informational only).
        return True, "(any)"

    def run_scenario(self, scenario: dict) -> list[ValidationResult]:
        case_id = self._prepare_case(scenario)
        results: list[ValidationResult] = []
        expectations = scenario.get("expectations", {})
        for payer_id in scenario.get("payers", ["DEFAULT"]):
            payer_review = self.service.review_with_payer(
                case_id, payer_id, self.settings
            )
            review = payer_review.review
            actual = review.recommendation.value
            expected = expectations.get(payer_id, {})
            passed, expected_label = self._check(expected, actual)
            results.append(
                ValidationResult(
                    scenario_id=scenario.get("scenario_id", "?"),
                    payer_id=payer_id,
                    passed=passed,
                    expected=expected_label,
                    actual=actual,
                    guideline_pack=review.guideline_pack or "",
                    guideline_version=review.guideline_version or "",
                    notes=scenario.get("title", ""),
                )
            )
        return results

    def run(self, scenarios: list[dict] | None = None) -> ValidationReport:
        scenarios = scenarios if scenarios is not None else load_default_scenarios()
        report = ValidationReport()
        for scenario in scenarios:
            report.results.extend(self.run_scenario(scenario))
        return report
