"""Validation entrypoints for external benchmark and formulary imports.

These helpers keep external-source orchestration out of the core importers:
the importer modules normalize source data, while this module runs validation
workflows, produces scores, and writes sync artifacts.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.importers.classifymymeds import (
    ClassifyMyMedsBenchmarkCase,
    ClassifyMyMedsBenchmarkImporter,
    ClassifyMyMedsImportSummary,
)
from app.importers.davinci_formulary import (
    DaVinciFormularyAdapter,
    FormularyCatalog,
)
from app.models.review_result import Recommendation


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FORMULARY_OUTPUT = PROJECT_ROOT / "validation" / "davinci_formulary_catalog.json"

_DIM_PA_NAMES = ("dim_pa.csv",)
_DIM_CLAIMS_NAMES = ("dim_claims.csv", "dim_claim.csv")
_DIM_DATE_NAMES = ("dim_date.csv", "dim_dates.csv")
_BRIDGE_NAMES = (
    "bridge.csv",
    "bridge_table.csv",
    "fact_claim_pa_date.csv",
    "fact_table.csv",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClassifyMyMedsScoreResult(BaseModel):
    """One scored ClassifyMyMeds benchmark case."""

    case_id: str
    expected: Recommendation
    predicted: Recommendation
    passed: bool
    criteria_labels: dict[str, bool] = Field(default_factory=dict)
    failure_reasons: list[str] = Field(default_factory=list)
    source_ids: dict[str, str] = Field(default_factory=dict)
    payer_bin: str | None = None
    drug: str | None = None


class ClassifyMyMedsBenchmarkReport(BaseModel):
    """Import + scoring report for ClassifyMyMeds."""

    generated_at: str = Field(default_factory=_utc_now_iso)
    dataset: str
    total_cases: int
    passed: int
    failed: int
    accuracy: float
    import_summary: ClassifyMyMedsImportSummary
    results: list[ClassifyMyMedsScoreResult] = Field(default_factory=list)

    @property
    def mismatches(self) -> list[ClassifyMyMedsScoreResult]:
        return [result for result in self.results if not result.passed]


class DaVinciFormularySyncReport(BaseModel):
    """Summary of a Da Vinci formulary sync."""

    generated_at: str = Field(default_factory=_utc_now_iso)
    source: str
    source_type: str
    output_path: str | None = None
    plans: int
    drugs: int
    items: int
    prior_authorization_required: int
    step_therapy_required: int
    quantity_limit: int
    by_tier: dict[str, int] = Field(default_factory=dict)

    @field_validator("source_type", mode="before")
    @classmethod
    def _normalize_source_type(cls, value):
        return str(value).strip().lower()


def discover_classifymymeds_files(dataset_dir: str | Path) -> dict[str, Path]:
    """Find common ClassifyMyMeds CSV filenames under a cloned dataset repo."""

    root = Path(dataset_dir)
    if not root.exists():
        raise FileNotFoundError(f"ClassifyMyMeds dataset directory not found: {root}")
    if not root.is_dir():
        raise ValueError(f"ClassifyMyMeds dataset path is not a directory: {root}")

    return {
        "dim_pa_csv": _find_named_file(root, _DIM_PA_NAMES, required=True),
        "bridge_csv": _find_named_file(root, _BRIDGE_NAMES),
        "dim_claims_csv": _find_named_file(root, _DIM_CLAIMS_NAMES),
        "dim_date_csv": _find_named_file(root, _DIM_DATE_NAMES),
    }


def run_classifymymeds_benchmark(
    *,
    dataset_dir: str | Path | None = None,
    dim_pa_csv: str | Path | None = None,
    bridge_csv: str | Path | None = None,
    dim_claims_csv: str | Path | None = None,
    dim_date_csv: str | Path | None = None,
    limit: int | None = None,
) -> ClassifyMyMedsBenchmarkReport:
    """Import ClassifyMyMeds rows and score a deterministic structured baseline.

    The public ClassifyMyMeds data is a structured simulated PA dataset, not a
    clinical-note corpus. This scorer therefore evaluates a baseline policy over
    the provided labels: approve only when diagnosis and step-therapy labels are
    true and contraindication is false.
    """

    if dataset_dir is not None:
        discovered = discover_classifymymeds_files(dataset_dir)
        dim_pa_csv = dim_pa_csv or discovered["dim_pa_csv"]
        bridge_csv = bridge_csv or discovered.get("bridge_csv")
        dim_claims_csv = dim_claims_csv or discovered.get("dim_claims_csv")
        dim_date_csv = dim_date_csv or discovered.get("dim_date_csv")

    if dim_pa_csv is None:
        raise ValueError("dim_pa_csv is required unless dataset_dir is provided.")

    importer = ClassifyMyMedsBenchmarkImporter()
    cases = importer.load_cases(
        dim_pa_csv=dim_pa_csv,
        bridge_csv=bridge_csv,
        dim_claims_csv=dim_claims_csv,
        dim_date_csv=dim_date_csv,
        limit=limit,
    )
    results = [_score_classifymymeds_case(case) for case in cases]
    passed = sum(1 for result in results if result.passed)
    total = len(results)
    return ClassifyMyMedsBenchmarkReport(
        dataset=str(dataset_dir or Path(dim_pa_csv).parent),
        total_cases=total,
        passed=passed,
        failed=total - passed,
        accuracy=round(passed / total, 4) if total else 0.0,
        import_summary=importer.summarize(cases),
        results=results,
    )


def sync_davinci_formulary(
    *,
    source: str | Path,
    source_type: str = "auto",
    output_path: str | Path | None = DEFAULT_FORMULARY_OUTPUT,
    resource_types: list[str] | None = None,
    timeout_seconds: int = 30,
) -> tuple[FormularyCatalog, DaVinciFormularySyncReport]:
    """Load Da Vinci FHIR resources and optionally write a normalized catalog."""

    resolved_type = _infer_formulary_source_type(source, source_type)
    adapter = DaVinciFormularyAdapter()
    if resolved_type == "directory":
        catalog = adapter.from_directory(source)
    elif resolved_type == "ndjson":
        catalog = adapter.from_ndjson(source)
    elif resolved_type == "bundle":
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        catalog = adapter.from_bundle(payload)
    elif resolved_type == "url":
        catalog = adapter.fetch_catalog(
            str(source),
            resource_types=resource_types,
            timeout_seconds=timeout_seconds,
        )
    else:
        raise ValueError(f"Unsupported formulary source type: {source_type}")

    output = None
    if output_path is not None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(catalog.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    report = _formulary_report(
        catalog,
        source=str(source),
        source_type=resolved_type,
        output_path=str(output) if output else None,
    )
    return catalog, report


def _score_classifymymeds_case(
    case: ClassifyMyMedsBenchmarkCase,
) -> ClassifyMyMedsScoreResult:
    predicted = _predict_classifymymeds(case)
    expected = case.expected_recommendation
    return ClassifyMyMedsScoreResult(
        case_id=case.case_id,
        expected=expected,
        predicted=predicted,
        passed=predicted is expected,
        criteria_labels=case.criteria_labels,
        failure_reasons=_classifymymeds_failure_reasons(case),
        source_ids=case.source_ids,
        payer_bin=case.payer_bin,
        drug=case.drug,
    )


def _predict_classifymymeds(case: ClassifyMyMedsBenchmarkCase) -> Recommendation:
    if case.pa.contraindication:
        return Recommendation.DENY
    if case.pa.correct_diagnosis and case.pa.tried_and_failed:
        return Recommendation.APPROVE
    return Recommendation.DENY


def _classifymymeds_failure_reasons(case: ClassifyMyMedsBenchmarkCase) -> list[str]:
    reasons: list[str] = []
    if not case.pa.correct_diagnosis:
        reasons.append("correct_diagnosis_not_established")
    if not case.pa.tried_and_failed:
        reasons.append("step_therapy_not_established")
    if case.pa.contraindication:
        reasons.append("contraindication_present")
    return reasons


def _formulary_report(
    catalog: FormularyCatalog,
    *,
    source: str,
    source_type: str,
    output_path: str | None,
) -> DaVinciFormularySyncReport:
    by_tier = Counter(item.drug_tier or "UNKNOWN" for item in catalog.items)
    return DaVinciFormularySyncReport(
        source=source,
        source_type=source_type,
        output_path=output_path,
        plans=len(catalog.plans),
        drugs=len(catalog.drugs),
        items=len(catalog.items),
        prior_authorization_required=sum(
            1 for item in catalog.items if item.prior_authorization_required is True
        ),
        step_therapy_required=sum(
            1 for item in catalog.items if item.step_therapy_required is True
        ),
        quantity_limit=sum(1 for item in catalog.items if item.quantity_limit is True),
        by_tier=dict(by_tier),
    )


def _infer_formulary_source_type(source: str | Path, source_type: str) -> str:
    requested = source_type.strip().lower()
    if requested != "auto":
        return requested

    source_text = str(source)
    if source_text.startswith(("http://", "https://")):
        return "url"

    path = Path(source)
    if path.is_dir():
        return "directory"
    if path.suffix.lower() == ".ndjson":
        return "ndjson"
    if path.suffix.lower() == ".json":
        return "bundle"
    raise ValueError(f"Cannot infer formulary source type for: {source}")


def _find_named_file(
    root: Path,
    candidates: tuple[str, ...],
    *,
    required: bool = False,
) -> Path | None:
    wanted = {name.lower() for name in candidates}
    for path in root.rglob("*"):
        if path.is_file() and path.name.lower() in wanted:
            return path
    if required:
        expected = ", ".join(candidates)
        raise FileNotFoundError(f"Expected one of {expected} under {root}")
    return None
