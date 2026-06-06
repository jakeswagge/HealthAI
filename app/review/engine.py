"""Deterministic clinical review engine.

Given a :class:`PatientCase` (and optionally the source document text as
supporting evidence), this engine:

1. Matches the case to the most relevant :class:`ClinicalGuideline`.
2. Evaluates each required criterion against the available evidence.
3. Detects contraindications.
4. Produces a validated :class:`ReviewResult` with a recommendation
   (APPROVE / DENY / INSUFFICIENT_INFORMATION), matched/missing criteria,
   missing evidence, recommended actions, rationale, and a confidence score.

Evidence model
--------------
The denial reason describes a *deficiency* (what the payer found lacking),
while the diagnosis, requested service, and document text provide *supporting*
evidence. A criterion is therefore:

- UNMET  if its keywords appear in the denial/deficiency text.
- MET    if its keywords appear in the supporting evidence (and it is not
         flagged as a deficiency).
- UNKNOWN if there is no evidence either way.

This engine is fully offline and independent of the extraction engine.
"""

from __future__ import annotations

import re

from app.guidelines.repository import (
    GuidelineRepository,
    get_default_repository,
)
from app.models.clinical_guideline import ClinicalGuideline, GuidelineCriterion
from app.models.patient_case import PatientCase
from app.models.review_result import (
    CriterionEvaluation,
    Recommendation,
    ReviewResult,
)


SPECIALIST_VOCABULARY = [
    "rheumatology",
    "evaluated by rheumatology",
    "rheumatology clinic",
    "rheumatology consultation",
    "specialist consultation",
    "specialist evaluation",
    "evaluated by specialist",
    "seen by specialist",
    "under care of rheumatology",
    "referred to rheumatology",
    "board-certified rheumatologist",
    "consulting rheumatologist",
    "reviewed by rheumatology service",
]

TB_SCREEN_VOCABULARY = [
    "quantiferon",
    "quantiferon gold",
    "quantiferon-tb",
    "quantiferon-tb gold negative",
    "t-spot",
    "tb test negative",
    "tuberculosis screening negative",
    "tuberculosis test negative",
    "latent tb screening",
    "negative tb result",
]

STEP_THERAPY_VOCABULARY = [
    "failed methotrexate",
    "methotrexate trial",
    "inadequate response to methotrexate",
    "persistent symptoms despite methotrexate",
    "dmard failure",
    "conventional dmard failure",
    "methotrexate ineffective",
    "uncontrolled disease on methotrexate",
    "refractory to methotrexate",
]

_NEGATION_BEFORE_RE = re.compile(
    r"\b(no|not|without|absent|missing|lacks?|lack of|undocumented)\b"
    r"(?:\W+\w+){0,5}\W*$",
    re.IGNORECASE,
)
_NEGATION_AFTER_RE = re.compile(
    r"^\W*(?:\w+\W+){0,4}"
    r"\b(not documented|not performed|not available|was not performed|were not performed)\b",
    re.IGNORECASE,
)


def _keyword_pattern(keyword: str) -> re.Pattern:
    escaped = re.escape(keyword.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\-", r"[-\s]?")
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def _is_negated_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 80):start]
    after = text[end : min(len(text), end + 80)]
    return bool(_NEGATION_BEFORE_RE.search(before) or _NEGATION_AFTER_RE.search(after))


def _expanded_keywords(crit: GuidelineCriterion) -> list[str]:
    keywords = list(crit.keywords)
    marker = f"{crit.id} {crit.description}".lower()
    if "specialist" in marker or "rheumatologist" in marker:
        keywords.extend(SPECIALIST_VOCABULARY)
    if "tb_screen" in marker or "tuberculosis" in marker:
        keywords.extend(TB_SCREEN_VOCABULARY)
    if "step_therapy" in marker or "dmard" in marker or "methotrexate" in marker:
        keywords.extend(STEP_THERAPY_VOCABULARY)
    unique = list(dict.fromkeys(k for k in keywords if k.strip()))
    return sorted(unique, key=len, reverse=True)


def _contains_any(
    text: str,
    keywords: list[str],
    *,
    ignore_negated: bool = False,
) -> str | None:
    """Return the first deterministic keyword/phrase found in text, or None."""
    for kw in keywords:
        k = kw.strip()
        if not k:
            continue
        for match in _keyword_pattern(k).finditer(text):
            if ignore_negated and _is_negated_context(text, match.start(), match.end()):
                continue
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


class ClinicalReviewEngine:
    """Rule-based clinical guideline review."""

    def __init__(self, repository: GuidelineRepository | None = None):
        self.repository = repository or get_default_repository()

    # ------------------------------------------------------------------ #
    # Evidence assembly
    # ------------------------------------------------------------------ #
    @staticmethod
    def _support_text(case: PatientCase, document_text: str | None) -> str:
        parts = [
            case.diagnosis or "",
            case.requested_service or "",
            " ".join(case.icd10_codes),
            " ".join(case.cpt_codes),
            document_text or "",
        ]
        return " \n ".join(parts).lower()

    @staticmethod
    def _deficiency_text(case: PatientCase, document_text: str | None) -> str:
        # The denial reason is the primary deficiency signal. We do NOT fold in
        # the full document here so that supporting evidence is not mistaken for
        # a deficiency.
        return (case.denial_reason or "").lower()

    # ------------------------------------------------------------------ #
    # Core review
    # ------------------------------------------------------------------ #
    def review(
        self,
        case: PatientCase,
        document_text: str | None = None,
    ) -> ReviewResult:
        """Review a patient case against the matched clinical guideline."""
        match = self.repository.match(case)

        if match is None:
            return self._no_guideline_result(case)

        guideline = match.guideline
        support = self._support_text(case, document_text)
        deficiency = self._deficiency_text(case, document_text)

        matched_criteria: list[str] = []
        missing_criteria: list[str] = []
        missing_evidence: list[str] = []
        unknown_criteria: list[str] = []
        detail: list[CriterionEvaluation] = []

        for crit in guideline.required_criteria:
            status, note = self._evaluate_criterion(crit, support, deficiency)
            detail.append(
                CriterionEvaluation(
                    id=crit.id, description=crit.description, met=(status == "met"), note=note
                )
            )
            if status == "met":
                matched_criteria.append(crit.description)
            elif status == "unmet":
                missing_criteria.append(crit.description)
                missing_evidence.append(
                    f"Evidence for: {crit.description}"
                )
            else:  # unknown
                unknown_criteria.append(crit.description)
                missing_criteria.append(crit.description)
                missing_evidence.append(
                    f"Documentation needed to establish: {crit.description}"
                )

        # Contraindications.
        contraindications_found: list[str] = []
        for contra in guideline.contraindications:
            hit = _contains_any(support, contra.keywords) or _contains_any(
                deficiency, contra.keywords
            )
            if hit:
                contraindications_found.append(contra.description)

        recommendation, confidence = self._decide(
            n_met=len(matched_criteria),
            n_unmet=len(missing_criteria) - len(unknown_criteria),
            n_unknown=len(unknown_criteria),
            n_required=guideline.required_count(),
            has_contraindication=bool(contraindications_found),
        )

        rationale = self._build_rationale(
            case,
            guideline,
            recommendation,
            matched_criteria,
            missing_criteria,
            unknown_criteria,
            contraindications_found,
        )

        recommended_actions = self._recommended_actions(
            recommendation, missing_criteria, contraindications_found
        )

        return ReviewResult(
            recommendation=recommendation,
            matched_criteria=matched_criteria,
            missing_criteria=missing_criteria,
            rationale=rationale,
            confidence_score=confidence,
            guideline_id=guideline.guideline_id,
            service_name=guideline.service_name,
            missing_evidence=missing_evidence,
            recommended_actions=recommended_actions,
            contraindications_found=contraindications_found,
            criteria_detail=detail,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _evaluate_criterion(
        crit: GuidelineCriterion, support: str, deficiency: str
    ) -> tuple[str, str | None]:
        """Return (status, note) where status in {met, unmet, unknown}."""
        keywords = _expanded_keywords(crit)
        flagged = _contains_any(deficiency, keywords)
        supported = _contains_any(support, keywords, ignore_negated=True)

        if flagged:
            return "unmet", f"Denial references this criterion ('{flagged}')."
        if supported:
            return "met", f"Supporting evidence found ('{supported}')."
        return "unknown", "No evidence found in the case or documentation."

    @staticmethod
    def _decide(
        n_met: int,
        n_unmet: int,
        n_unknown: int,
        n_required: int,
        has_contraindication: bool,
    ) -> tuple[Recommendation, float]:
        """Determine recommendation and confidence."""
        if has_contraindication:
            return Recommendation.DENY, 0.9

        if n_required == 0:
            return Recommendation.INSUFFICIENT_INFORMATION, 0.4

        if n_unmet > 0:
            # At least one criterion is explicitly contradicted/deficient.
            confidence = round(min(0.95, 0.7 + 0.1 * n_unmet), 3)
            return Recommendation.DENY, confidence

        if n_met == n_required:
            return Recommendation.APPROVE, 0.9

        # Some criteria are simply unestablished (unknown), none contradicted.
        # Not enough to approve, not enough to deny.
        confidence = round(0.5 + 0.1 * (n_met / max(1, n_required)), 3)
        return Recommendation.INSUFFICIENT_INFORMATION, confidence

    @staticmethod
    def _build_rationale(
        case: PatientCase,
        guideline: ClinicalGuideline,
        recommendation: Recommendation,
        matched: list[str],
        missing: list[str],
        unknown: list[str],
        contraindications: list[str],
    ) -> str:
        svc = guideline.service_name
        lines = [
            f"Reviewed request for {svc} against {guideline.guideline_id} "
            f"({guideline.source}, v{guideline.version})."
        ]
        if contraindications:
            lines.append(
                "Contraindication(s) present: " + "; ".join(contraindications) + "."
            )
        if matched:
            lines.append(f"{len(matched)} criterion/criteria met.")
        if missing:
            lines.append(
                f"{len(missing)} criterion/criteria not satisfied: "
                + "; ".join(missing)
                + "."
            )

        if recommendation is Recommendation.APPROVE:
            lines.append(
                "All required medical-necessity criteria are satisfied; the "
                "request meets guideline criteria for approval."
            )
        elif recommendation is Recommendation.DENY:
            lines.append(
                "One or more required criteria are unmet (or a contraindication "
                "exists); a denial is justified under the guideline until the "
                "missing requirements are documented."
            )
        else:
            lines.append(
                "There is insufficient documentation to confirm one or more "
                "criteria. Additional evidence is required before a "
                "determination can be made."
            )
        return " ".join(lines)

    @staticmethod
    def _recommended_actions(
        recommendation: Recommendation,
        missing: list[str],
        contraindications: list[str],
    ) -> list[str]:
        actions: list[str] = []
        if recommendation is Recommendation.APPROVE:
            actions.append("Proceed with authorization; document approval in the record.")
        elif recommendation is Recommendation.DENY:
            if contraindications:
                actions.append(
                    "Address/resolve the contraindication before resubmission."
                )
            for m in missing:
                actions.append(f"Submit documentation for: {m}")
            actions.append(
                "If criteria are met but undocumented, file an appeal with the "
                "supporting clinical records."
            )
        else:
            for m in missing:
                actions.append(f"Obtain and submit documentation for: {m}")
            actions.append("Resubmit the prior authorization once evidence is complete.")
        return actions

    @staticmethod
    def _no_guideline_result(case: PatientCase) -> ReviewResult:
        svc = case.requested_service or "the requested service"
        return ReviewResult(
            recommendation=Recommendation.INSUFFICIENT_INFORMATION,
            matched_criteria=[],
            missing_criteria=[],
            rationale=(
                f"No clinical guideline in the library matched {svc!r}. "
                "A determination cannot be made automatically; manual review "
                "is required."
            ),
            confidence_score=0.3,
            guideline_id=None,
            service_name=case.requested_service,
            missing_evidence=["A matching clinical guideline for the requested service."],
            recommended_actions=[
                "Route to a human reviewer.",
                "Consider adding a guideline for this service to the library.",
            ],
        )
