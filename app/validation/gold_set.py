"""Human-adjudicated clinical-review gold-set loading.

The validation runner can still use the legacy seed scenarios, but this module
now also supports explicit reviewer-adjudicated JSON files with case labels,
criterion labels, evidence ids, conflict flags, and reviewer rationale.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.cases.service import CaseService
from app.models.review_result import Recommendation
from app.storage.database import connect, initialize_schema
from app.tests.review_scenarios import ALL_SCENARIOS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATRIX_GOLD_SET = PROJECT_ROOT / "validation" / "test_matrix_cases.json"


class AdjudicatedCriterionLabel(BaseModel):
    """Human label for one criterion in a gold case."""

    criterion_id: str
    status: str
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value):
        text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
        if text in {"MET", "NOT_MET", "UNKNOWN"}:
            return text
        if text in {"UNMET", "MISSING", "FAILED"}:
            return "NOT_MET"
        return "UNKNOWN"


class AdjudicatedGoldCase(BaseModel):
    """One adjudicated case suitable for clinical-accuracy validation."""

    case_id: str
    expected: Recommendation
    documents: list[dict] = Field(default_factory=list)
    criteria: list[AdjudicatedCriterionLabel] = Field(default_factory=list)
    slices: list[str] = Field(default_factory=list)
    conflict_flags: list[str] = Field(default_factory=list)
    reviewer: str = ""
    reviewer_rationale: str = ""
    locked_holdout: bool = False
    source: str = "human_adjudicated"

    @field_validator("expected", mode="before")
    @classmethod
    def _normalize_expected(cls, value):
        text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
        if text == "HUMAN_REVIEW":
            return Recommendation.INSUFFICIENT_INFORMATION
        return text

    @field_validator("slices", "conflict_flags", mode="before")
    @classmethod
    def _coerce_str_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [str(item).strip().lower() for item in value if str(item).strip()]


def load_adjudicated_clinical_gold_set(path: str | Path) -> list[dict]:
    """Load a reviewer-adjudicated gold-set JSON file into review scenarios."""

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw_cases = raw.get("cases", [])
    else:
        raw_cases = raw
    scenarios: list[dict] = []
    for item in raw_cases:
        gold = AdjudicatedGoldCase.model_validate(item)
        scenarios.append(_adjudicated_case_to_scenario(gold, source_path=Path(path)))
    return scenarios


def load_seed_clinical_gold_set() -> list[dict]:
    """Return the local seed clinical-review gold set."""

    scenarios: list[dict] = []
    for scenario in deepcopy(ALL_SCENARIOS):
        scenario["case_id"] = scenario.get("name")
        scenario["slices"] = _infer_slices(scenario)
        scenario["adjudication"] = {
            "source": "local_seed_review_scenarios",
            "reviewer_rationale": (
                "Seed label inherited from deterministic clinical-review scenario. "
                "Promote to locked holdout only after human adjudication."
            ),
        }
        scenarios.append(scenario)
    return scenarios


def load_matrix_clinical_gold_set(
    path: str | Path = DEFAULT_MATRIX_GOLD_SET,
) -> list[dict]:
    """Load document-backed matrix cases as clinical-review gold scenarios."""

    raw_cases = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios: list[dict] = []
    for item in raw_cases:
        scenarios.append(_matrix_case_to_scenario(item, source_path=Path(path)))
    return scenarios


def _adjudicated_case_to_scenario(
    gold: AdjudicatedGoldCase,
    *,
    source_path: Path,
) -> dict:
    context, texts = _assemble_documents(gold.case_id, gold.documents)
    slices = gold.slices or _infer_slices(
        {
            "name": gold.case_id,
            "document_text": "\n".join(texts),
            "case": context.patient_case,
        }
    )
    return {
        "case_id": gold.case_id,
        "name": gold.case_id,
        "expected": gold.expected.value,
        "case": context.patient_case,
        "document_text": "\n\n".join(texts),
        "slices": list(dict.fromkeys(slices)),
        "adjudication": {
            "source": str(source_path),
            "reviewer": gold.reviewer,
            "reviewer_rationale": gold.reviewer_rationale,
            "criteria": [item.model_dump(mode="json") for item in gold.criteria],
            "conflict_flags": gold.conflict_flags,
            "locked_holdout": gold.locked_holdout,
        },
    }


def _matrix_case_to_scenario(item: dict, *, source_path: Path) -> dict:
    context, texts = _assemble_documents(
        item.get("case_id") or item.get("scenario") or "matrix-case",
        item.get("documents", []),
    )

    expected = item.get("expected", {})
    expected_rec = str(expected.get("recommendation") or "INSUFFICIENT_INFORMATION")
    if expected_rec.upper() == "HUMAN_REVIEW":
        expected_rec = "INSUFFICIENT_INFORMATION"

    return {
        "case_id": item.get("case_id") or item.get("scenario"),
        "name": item.get("scenario") or item.get("case_id"),
        "expected": expected_rec.upper(),
        "expected_human_review": bool(expected.get("human_review")),
        "case": context.patient_case,
        "document_text": "\n\n".join(texts),
        "slices": _infer_matrix_slices(item),
        "adjudication": {
            "source": str(source_path),
            "reviewer_rationale": item.get("scenario") or "",
            "expected_facts": expected,
        },
    }


def _assemble_documents(case_id: str, documents: list[dict]):
    conn = connect(":memory:")
    try:
        initialize_schema(conn)
        service = CaseService(conn=conn)
        record = service.create_case(case_id)
        texts: list[str] = []
        for doc in documents:
            text = doc.get("text", "")
            texts.append(text)
            service.ingest_document(
                record.case_id,
                doc.get("filename") or "document.txt",
                text.encode("utf-8"),
            )
        return service.assemble_case(record.case_id), texts
    finally:
        conn.close()


def _infer_matrix_slices(item: dict) -> list[str]:
    expected = item.get("expected", {})
    slices = _infer_slices(
        {
            "name": item.get("scenario") or item.get("case_id"),
            "document_text": "\n".join(
                str(doc.get("text", "")) for doc in item.get("documents", [])
            ),
        }
    )
    if expected.get("tb_state") is not None and "tb" not in slices:
        slices.append("tb")
    if expected.get("step_therapy_state") is not None and "step_therapy" not in slices:
        slices.append("step_therapy")
    if expected.get("provider_state") is not None and "specialist" not in slices:
        slices.append("specialist")
    return list(dict.fromkeys(slices))


def _infer_slices(scenario: dict) -> list[str]:
    text = " ".join(
        str(value)
        for value in (
            scenario.get("name", ""),
            scenario.get("expected_guideline", ""),
            scenario.get("document_text", ""),
            getattr(scenario.get("case"), "diagnosis", ""),
            getattr(scenario.get("case"), "requested_service", ""),
            getattr(scenario.get("case"), "denial_reason", ""),
        )
        if value
    ).lower()
    slices: list[str] = []
    if "humira" in text or "enbrel" in text or "adalimumab" in text or "etanercept" in text:
        slices.append("autoimmune_biologic")
    if "tb" in text or "tuberculosis" in text or "quantiferon" in text:
        slices.append("tb")
    if "step" in text or "dmard" in text or "methotrexate" in text or "azathioprine" in text:
        slices.append("step_therapy")
    if (
        "specialist" in text
        or "rheumatologist" in text
        or "dermatologist" in text
        or "gastroenterologist" in text
    ):
        slices.append("specialist")
    if "contraindication" in text or "active infection" in text:
        slices.append("contraindication")
    if "mri" in text:
        slices.append("imaging")
    if "ct chest" in text or "chest ct" in text:
        slices.append("imaging")
    if "physical therapy" in text or "physiotherapy" in text:
        slices.append("therapy")
    return list(dict.fromkeys(slices))
