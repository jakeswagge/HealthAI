"""Importer for the public ClassifyMyMeds simulated PA benchmark.

The upstream repository contains a simulated CoverMyMeds challenge dataset with
claim rows, PA rows, dates, and a bridge table. It is useful as a structured
approval benchmark, not as a clinical-note evidence-span dataset.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

from app.models.review_result import Recommendation


def _clean(value) -> str:
    return "" if value is None else str(value).strip()


def _bool(value) -> bool:
    text = _clean(value).lower()
    return text in {"1", "true", "t", "yes", "y"}


class ClassifyMyMedsPARecord(BaseModel):
    """One row from ``dim_pa.csv``."""

    pa_id: str
    correct_diagnosis: bool
    tried_and_failed: bool
    contraindication: bool
    pa_approved: bool

    @property
    def expected_recommendation(self) -> Recommendation:
        return Recommendation.APPROVE if self.pa_approved else Recommendation.DENY


class ClassifyMyMedsBenchmarkCase(BaseModel):
    """Joined benchmark case suitable for deterministic/ML evaluation."""

    case_id: str
    pa: ClassifyMyMedsPARecord
    payer_bin: str | None = None
    drug: str | None = None
    reject_code: str | None = None
    pharmacy_claim_approved: bool | None = None
    claim_date: str | None = None
    source_ids: dict[str, str] = Field(default_factory=dict)

    @property
    def expected_recommendation(self) -> Recommendation:
        return self.pa.expected_recommendation

    @property
    def criteria_labels(self) -> dict[str, bool]:
        return {
            "correct_diagnosis": self.pa.correct_diagnosis,
            "tried_and_failed": self.pa.tried_and_failed,
            "contraindication": self.pa.contraindication,
        }


class ClassifyMyMedsImportSummary(BaseModel):
    """Small aggregate report for sanity-checking imports."""

    total_cases: int
    approved_cases: int
    denied_cases: int
    by_drug: dict[str, int] = Field(default_factory=dict)
    by_payer_bin: dict[str, int] = Field(default_factory=dict)


class ClassifyMyMedsBenchmarkImporter:
    """Load ClassifyMyMeds CSV files into normalized benchmark records."""

    def load_pa_records(self, dim_pa_csv: str | Path) -> list[ClassifyMyMedsPARecord]:
        rows = self._read_csv(dim_pa_csv)
        records: list[ClassifyMyMedsPARecord] = []
        for row in rows:
            records.append(
                ClassifyMyMedsPARecord(
                    pa_id=_clean(row.get("dim_pa_id")),
                    correct_diagnosis=_bool(row.get("correct_diagnosis")),
                    tried_and_failed=_bool(row.get("tried_and_failed")),
                    contraindication=_bool(row.get("contraindication")),
                    pa_approved=_bool(row.get("pa_approved")),
                )
            )
        return records

    def load_cases(
        self,
        *,
        dim_pa_csv: str | Path,
        bridge_csv: str | Path | None = None,
        dim_claims_csv: str | Path | None = None,
        dim_date_csv: str | Path | None = None,
        limit: int | None = None,
    ) -> list[ClassifyMyMedsBenchmarkCase]:
        pa_by_id = {r.pa_id: r for r in self.load_pa_records(dim_pa_csv)}
        claim_by_id = self._by_id(dim_claims_csv, "dim_claim_id") if dim_claims_csv else {}
        date_by_id = self._by_id(dim_date_csv, "dim_date_id") if dim_date_csv else {}
        bridge_rows = self._read_csv(bridge_csv) if bridge_csv else []

        cases: list[ClassifyMyMedsBenchmarkCase] = []
        if bridge_rows:
            for row in bridge_rows:
                pa_id = _clean(row.get("dim_pa_id"))
                pa = pa_by_id.get(pa_id)
                if pa is None:
                    continue
                claim_id = _clean(row.get("dim_claim_id"))
                date_id = _clean(row.get("dim_date_id"))
                claim = claim_by_id.get(claim_id, {})
                date = date_by_id.get(date_id, {})
                cases.append(self._case_from_join(pa, claim, date, claim_id, date_id))
                if limit is not None and len(cases) >= limit:
                    return cases
            return cases

        for pa in pa_by_id.values():
            cases.append(self._case_from_join(pa, {}, {}, "", ""))
            if limit is not None and len(cases) >= limit:
                break
        return cases

    @staticmethod
    def summarize(cases: Iterable[ClassifyMyMedsBenchmarkCase]) -> ClassifyMyMedsImportSummary:
        items = list(cases)
        drug_counts = Counter(c.drug or "UNKNOWN" for c in items)
        payer_counts = Counter(c.payer_bin or "UNKNOWN" for c in items)
        approved = sum(1 for c in items if c.expected_recommendation is Recommendation.APPROVE)
        return ClassifyMyMedsImportSummary(
            total_cases=len(items),
            approved_cases=approved,
            denied_cases=len(items) - approved,
            by_drug=dict(drug_counts),
            by_payer_bin=dict(payer_counts),
        )

    @staticmethod
    def _read_csv(path: str | Path | None) -> list[dict[str, str]]:
        if path is None:
            return []
        with Path(path).open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))

    def _by_id(self, path: str | Path, key: str) -> dict[str, dict[str, str]]:
        return {_clean(row.get(key)): row for row in self._read_csv(path)}

    @staticmethod
    def _case_from_join(
        pa: ClassifyMyMedsPARecord,
        claim: dict[str, str],
        date: dict[str, str],
        claim_id: str,
        date_id: str,
    ) -> ClassifyMyMedsBenchmarkCase:
        source_ids = {"dim_pa_id": pa.pa_id}
        if claim_id:
            source_ids["dim_claim_id"] = claim_id
        if date_id:
            source_ids["dim_date_id"] = date_id
        return ClassifyMyMedsBenchmarkCase(
            case_id=f"CMM-PA-{pa.pa_id}",
            pa=pa,
            payer_bin=_clean(claim.get("bin")) or None,
            drug=_clean(claim.get("drug")) or None,
            reject_code=_clean(claim.get("reject_code")) or None,
            pharmacy_claim_approved=(
                _bool(claim.get("pharmacy_claim_approved"))
                if "pharmacy_claim_approved" in claim
                else None
            ),
            claim_date=_clean(date.get("date_val")) or None,
            source_ids=source_ids,
        )
