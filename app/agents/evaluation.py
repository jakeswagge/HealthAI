"""Evaluation framework for the MedicalExtractionAgent.

Measures, across the sample corpus:
- Field extraction accuracy (per-field correctness vs. ground truth)
- Missing-field handling (fields known to be absent must be null/empty)
- JSON validity (the backend returned parseable JSON)
- Schema compliance (output validated as a PatientCase)

The framework is backend-agnostic: pass any :class:`MedicalExtractionAgent`.
By default the local heuristic backend is used so evaluation is deterministic
and runs offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.agents.medical_extraction_agent import (
    ExtractionError,
    MedicalExtractionAgent,
)
from app.extraction.extractor import extract_text
from app.models.patient_case import PatientCase

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DOCS_DIR = PROJECT_ROOT / "data" / "sample_docs"


@dataclass
class DocEvaluation:
    """Evaluation result for a single document."""

    filename: str
    json_valid: bool
    schema_compliant: bool
    fields_checked: int = 0
    fields_correct: int = 0
    missing_ok: bool = True
    errors: list[str] = field(default_factory=list)
    case: PatientCase | None = None

    @property
    def field_accuracy(self) -> float:
        if self.fields_checked == 0:
            return 1.0
        return round(self.fields_correct / self.fields_checked, 4)


@dataclass
class EvaluationReport:
    """Aggregate evaluation across the corpus."""

    docs: list[DocEvaluation] = field(default_factory=list)

    @property
    def total_docs(self) -> int:
        return len(self.docs)

    @property
    def json_validity_rate(self) -> float:
        if not self.docs:
            return 0.0
        return round(sum(d.json_valid for d in self.docs) / len(self.docs), 4)

    @property
    def schema_compliance_rate(self) -> float:
        if not self.docs:
            return 0.0
        return round(
            sum(d.schema_compliant for d in self.docs) / len(self.docs), 4
        )

    @property
    def field_accuracy(self) -> float:
        checked = sum(d.fields_checked for d in self.docs)
        correct = sum(d.fields_correct for d in self.docs)
        if checked == 0:
            return 1.0
        return round(correct / checked, 4)

    @property
    def missing_field_handling_rate(self) -> float:
        if not self.docs:
            return 0.0
        return round(sum(d.missing_ok for d in self.docs) / len(self.docs), 4)

    def as_dict(self) -> dict:
        return {
            "total_docs": self.total_docs,
            "json_validity_rate": self.json_validity_rate,
            "schema_compliance_rate": self.schema_compliance_rate,
            "field_accuracy": self.field_accuracy,
            "missing_field_handling_rate": self.missing_field_handling_rate,
        }


def _check_fields(case: PatientCase, expected: dict, ev: DocEvaluation) -> None:
    """Compare a validated case against the ground-truth expectations."""

    def record(ok: bool, label: str):
        ev.fields_checked += 1
        if ok:
            ev.fields_correct += 1
        else:
            ev.errors.append(label)

    if "patient_name" in expected:
        record(case.patient_name == expected["patient_name"], "patient_name")
    if "member_id" in expected:
        record(case.member_id == expected["member_id"], "member_id")
    if "date_of_birth" in expected:
        record(case.date_of_birth == expected["date_of_birth"], "date_of_birth")
    if "decision" in expected:
        record(case.decision.value == expected["decision"], "decision")
    if "insurance_company_contains" in expected:
        val = (case.insurance_company or "").lower()
        record(
            expected["insurance_company_contains"].lower() in val,
            "insurance_company",
        )
    if "physician_contains" in expected:
        val = (case.physician_name or "").lower()
        record(expected["physician_contains"].lower() in val, "physician_name")
    if "icd10_includes" in expected:
        for code in expected["icd10_includes"]:
            record(code in case.icd10_codes, f"icd10:{code}")
    if "cpt_includes" in expected:
        for code in expected["cpt_includes"]:
            record(code in case.cpt_codes, f"cpt:{code}")
    if expected.get("denial_reason_required"):
        record(bool(case.denial_reason), "denial_reason_present")
    if expected.get("denial_reason_expected_none"):
        record(case.denial_reason is None, "denial_reason_absent")

    # Missing-field handling: fields known to be absent must be empty/null.
    for missing in expected.get("expected_missing", []):
        value = getattr(case, missing)
        ok = (value is None) or (value == []) or (value == "")
        if not ok:
            ev.missing_ok = False
            ev.errors.append(f"expected_missing:{missing}")


def evaluate_document(
    filename: str,
    expected: dict,
    agent: MedicalExtractionAgent,
) -> DocEvaluation:
    """Evaluate a single sample document against its ground truth."""
    ev = DocEvaluation(filename=filename, json_valid=False, schema_compliant=False)
    path = SAMPLE_DOCS_DIR / filename
    document = extract_text(path)

    try:
        result = agent.extract(document.text)
    except ExtractionError as exc:
        ev.errors.append(f"extraction_failed: {exc}")
        return ev

    # If we got here, JSON parsed and validated into a PatientCase.
    ev.json_valid = True
    ev.schema_compliant = isinstance(result.case, PatientCase)
    ev.case = result.case
    _check_fields(result.case, expected, ev)
    return ev


def run_evaluation(
    ground_truth: dict[str, dict],
    agent: MedicalExtractionAgent | None = None,
) -> EvaluationReport:
    """Run the full evaluation over a ground-truth mapping."""
    agent = agent or MedicalExtractionAgent()
    report = EvaluationReport()
    for filename, expected in ground_truth.items():
        report.docs.append(evaluate_document(filename, expected, agent))
    return report
