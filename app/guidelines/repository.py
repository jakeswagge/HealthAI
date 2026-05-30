"""Guideline repository: load + match clinical guidelines.

Guidelines are stored as local JSON files under ``data/guidelines``. This
module loads them into :class:`ClinicalGuideline` models and matches a
:class:`PatientCase` to the most relevant guideline using service name,
aliases, CPT codes, and ICD-10 prefixes.

Matching is deterministic and offline; it has no dependency on the review or
extraction engines.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.models.clinical_guideline import ClinicalGuideline
from app.models.patient_case import PatientCase

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GUIDELINES_DIR = PROJECT_ROOT / "data" / "guidelines"


@dataclass
class GuidelineMatch:
    """Result of matching a PatientCase to a guideline."""

    guideline: ClinicalGuideline
    score: float
    reasons: list[str]


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


class GuidelineRepository:
    """In-memory repository of clinical guidelines loaded from JSON."""

    def __init__(self, guidelines: list[ClinicalGuideline] | None = None):
        self._guidelines: list[ClinicalGuideline] = guidelines or []

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    @classmethod
    def from_directory(cls, directory: str | Path) -> "GuidelineRepository":
        """Load all ``*.json`` guideline files from a directory.

        Files that fail to parse/validate are skipped with a logged warning,
        so one malformed file never breaks the whole library.
        """
        directory = Path(directory)
        guidelines: list[ClinicalGuideline] = []
        if not directory.is_dir():
            print(f"[guidelines] WARNING: directory not found: {directory}")
            return cls(guidelines)

        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                guidelines.append(ClinicalGuideline.model_validate(data))
            except Exception as exc:  # noqa: BLE001 - skip bad files, keep going
                print(f"[guidelines] WARNING: skipping {path.name}: {exc}")
        return cls(guidelines)

    # ------------------------------------------------------------------ #
    # Access
    # ------------------------------------------------------------------ #
    def all(self) -> list[ClinicalGuideline]:
        return list(self._guidelines)

    def __len__(self) -> int:
        return len(self._guidelines)

    def get(self, guideline_id: str) -> Optional[ClinicalGuideline]:
        for g in self._guidelines:
            if g.guideline_id == guideline_id:
                return g
        return None

    # ------------------------------------------------------------------ #
    # Matching
    # ------------------------------------------------------------------ #
    def _score(self, case: PatientCase, g: ClinicalGuideline) -> GuidelineMatch:
        """Score how well a guideline matches a case."""
        score = 0.0
        reasons: list[str] = []

        service = _norm(case.requested_service)
        diagnosis = _norm(case.diagnosis)
        haystack = f"{service} {diagnosis}"

        # 1. Service name / alias match (strongest signal).
        names = [g.service_name] + g.aliases
        for name in names:
            n = _norm(name)
            if n and n in haystack:
                score += 5.0
                reasons.append(f"service/alias match: '{name}'")
                break

        # 2. CPT code match.
        case_cpts = {c.upper() for c in case.cpt_codes}
        if case_cpts and set(c.upper() for c in g.applicable_cpt) & case_cpts:
            score += 4.0
            reasons.append("CPT code match")

        # 3. ICD-10 prefix match.
        for code in case.icd10_codes:
            cu = code.upper()
            for prefix in g.applicable_icd10:
                if cu.startswith(prefix.upper()):
                    score += 2.0
                    reasons.append(f"ICD-10 prefix match: {prefix}")
                    break
            else:
                continue
            break

        # 4. Token overlap with the guideline's primary service words.
        svc_tokens = {t for t in _norm(g.service_name).split() if len(t) > 2}
        if svc_tokens and svc_tokens & set(haystack.split()):
            score += 0.5

        return GuidelineMatch(guideline=g, score=round(score, 3), reasons=reasons)

    def match(self, case: PatientCase) -> Optional[GuidelineMatch]:
        """Return the best matching guideline for a case, or None."""
        if not self._guidelines:
            return None
        scored = [self._score(case, g) for g in self._guidelines]
        scored.sort(key=lambda m: m.score, reverse=True)
        best = scored[0]
        if best.score <= 0.0:
            return None
        return best

    def match_all(self, case: PatientCase) -> list[GuidelineMatch]:
        """Return all guidelines scored, sorted best-first."""
        scored = [self._score(case, g) for g in self._guidelines]
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored


_DEFAULT_REPO: Optional[GuidelineRepository] = None


def get_default_repository(force_reload: bool = False) -> GuidelineRepository:
    """Return a cached repository loaded from the default guidelines dir."""
    global _DEFAULT_REPO
    if _DEFAULT_REPO is None or force_reload:
        _DEFAULT_REPO = GuidelineRepository.from_directory(DEFAULT_GUIDELINES_DIR)
    return _DEFAULT_REPO
