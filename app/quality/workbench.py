"""ReviewerWorkbench: reviewer-facing evidence validation tooling.

Combines an evidence reference with its quality assessment, supporting/
conflicting siblings, and the reviewer's latest decision, and records
APPROVE / REJECT / FLAG decisions (append-only + audited via the caller).

This is a thin orchestration layer over the quality + decision repositories;
it does not itself write audit events (the CaseService does, to keep audit in
one place), but it exposes everything the UI and service need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.models.evidence_quality import EvidenceQualityAssessment
from app.models.evidence_reference import EvidenceReference
from app.models.evidence_review_decision import (
    EvidenceDecision,
    EvidenceReviewDecision,
)
from app.quality.decision_repository import EvidenceReviewDecisionRepository
from app.quality.repository import EvidenceQualityRepository


def _value_of(ref: EvidenceReference) -> str:
    return ref.normalized_fact.split(": ", 1)[-1] if ref.normalized_fact else ""


@dataclass
class EvidenceView:
    """A reviewer-facing bundle for one evidence reference."""

    evidence: EvidenceReference
    quality: Optional[EvidenceQualityAssessment] = None
    supporting: list[EvidenceReference] = field(default_factory=list)
    conflicting: list[EvidenceReference] = field(default_factory=list)
    latest_decision: Optional[EvidenceReviewDecision] = None

    @property
    def status(self) -> str:
        if self.latest_decision is None:
            return "PENDING"
        return self.latest_decision.decision.value


class ReviewerWorkbench:
    """Assemble evidence views and record reviewer decisions."""

    def __init__(
        self,
        quality_repo: EvidenceQualityRepository,
        decision_repo: EvidenceReviewDecisionRepository,
    ) -> None:
        self.quality = quality_repo
        self.decisions = decision_repo

    def build_views(
        self,
        evidence: list[EvidenceReference],
    ) -> list[EvidenceView]:
        """Build a view per evidence reference with quality + relations."""
        # Index siblings by fact_type for supporting/conflicting computation.
        by_fact: dict[str, list[EvidenceReference]] = {}
        for ev in evidence:
            by_fact.setdefault(ev.fact_type or "other", []).append(ev)

        views: list[EvidenceView] = []
        for ev in evidence:
            siblings = [s for s in by_fact.get(ev.fact_type or "other", []) if s.evidence_id != ev.evidence_id]
            supporting = [s for s in siblings if _value_of(s).lower() == _value_of(ev).lower()]
            conflicting = [
                s for s in siblings
                if _value_of(s).lower() != _value_of(ev).lower()
                and (ev.fact_type not in {"icd10_codes", "cpt_codes"})
            ]
            views.append(
                EvidenceView(
                    evidence=ev,
                    quality=self.quality.for_evidence(ev.evidence_id),
                    supporting=supporting,
                    conflicting=conflicting,
                    latest_decision=self.decisions.latest_for_evidence(ev.evidence_id),
                )
            )
        return views

    def record_decision(
        self,
        evidence_id: str,
        case_id: str,
        reviewer: str,
        decision: EvidenceDecision | str,
        comments: str = "",
    ) -> EvidenceReviewDecision:
        """Persist a reviewer decision about an evidence reference."""
        if not reviewer or not reviewer.strip():
            raise ValueError("reviewer is required for an evidence decision.")
        d = EvidenceReviewDecision(
            evidence_id=evidence_id,
            case_id=case_id,
            reviewer=reviewer.strip(),
            decision=decision,
            comments=comments,
        )
        return self.decisions.add(d)

    def approved_evidence_ids(self, case_id: str) -> set[str]:
        """Evidence ids whose latest decision is APPROVE."""
        out: set[str] = set()
        seen: dict[str, EvidenceReviewDecision] = {}
        for d in self.decisions.for_case(case_id):
            seen[d.evidence_id] = d  # later ones win (ordered ASC)
        for evidence_id, d in seen.items():
            if d.decision is EvidenceDecision.APPROVE:
                out.add(evidence_id)
        return out

    def rejected_evidence_ids(self, case_id: str) -> set[str]:
        """Evidence ids whose latest decision is REJECT."""
        out: set[str] = set()
        seen: dict[str, EvidenceReviewDecision] = {}
        for d in self.decisions.for_case(case_id):
            seen[d.evidence_id] = d
        for evidence_id, d in seen.items():
            if d.decision is EvidenceDecision.REJECT:
                out.add(evidence_id)
        return out
