"""ValidatedEvidenceEngine: apply governance policy to a case's evidence.

Given the evidence, reviewer decisions, quality assessments, and the active
:class:`GovernanceSettings`, this produces an :class:`ApprovedEvidenceSet`
listing which evidence is included and which is excluded (with a reason).

Filtering rules (validated mode):
1. REJECTED evidence is ALWAYS excluded (reviewer authority wins).
2. Evidence below ``minimum_quality_score`` is excluded.
3. If ``allow_unreviewed_evidence`` is False, evidence with no APPROVE decision
   is excluded.
4. Everything else is included.

In draft mode all evidence is included (current default behavior preserved).
The engine is pure/deterministic; the caller records the audit event.
"""

from __future__ import annotations

from app.models.evidence_quality import EvidenceQualityAssessment
from app.models.evidence_reference import EvidenceReference
from app.models.evidence_review_decision import EvidenceDecision
from app.models.governance import (
    ApprovedEvidenceSet,
    EvidenceMode,
    ExcludedEvidence,
    GovernanceSettings,
)


def _value_of(ref: EvidenceReference) -> str:
    return ref.normalized_fact.split(": ", 1)[-1] if ref.normalized_fact else ""


class ValidatedEvidenceEngine:
    """Produce a governance-filtered evidence set for downstream consumers."""

    def build_set(
        self,
        case_id: str,
        evidence: list[EvidenceReference],
        settings: GovernanceSettings,
        *,
        approved_ids: set[str] | None = None,
        rejected_ids: set[str] | None = None,
        flagged_ids: set[str] | None = None,
        quality_by_id: dict[str, EvidenceQualityAssessment] | None = None,
    ) -> ApprovedEvidenceSet:
        """Apply governance settings and return the included/excluded split."""
        approved_ids = approved_ids or set()
        rejected_ids = rejected_ids or set()
        flagged_ids = flagged_ids or set()
        quality_by_id = quality_by_id or {}

        snapshot = settings.model_dump()

        # Draft mode: everything is included.
        if not settings.validated_evidence_mode:
            return ApprovedEvidenceSet(
                case_id=case_id,
                mode=EvidenceMode.DRAFT,
                included_ids=[e.evidence_id for e in evidence],
                excluded=[],
                settings_snapshot=snapshot,
            )

        included: list[str] = []
        excluded: list[ExcludedEvidence] = []

        for ev in evidence:
            eid = ev.evidence_id

            # 1. Reviewer authority: rejected is always excluded.
            if eid in rejected_ids:
                excluded.append(self._excluded(ev, "rejected by reviewer"))
                continue

            # 2. Quality threshold.
            if settings.minimum_quality_score > 0.0:
                q = quality_by_id.get(eid)
                score = q.overall_score if q else 0.0
                if score < settings.minimum_quality_score:
                    excluded.append(
                        self._excluded(
                            ev,
                            f"quality {score:.2f} below minimum "
                            f"{settings.minimum_quality_score:.2f}",
                        )
                    )
                    continue

            # 3. Unreviewed gate.
            if not settings.allow_unreviewed_evidence and eid not in approved_ids:
                excluded.append(
                    self._excluded(ev, "not approved by a reviewer")
                )
                continue

            included.append(eid)

        return ApprovedEvidenceSet(
            case_id=case_id,
            mode=EvidenceMode.VALIDATED,
            included_ids=included,
            excluded=excluded,
            settings_snapshot=snapshot,
        )

    @staticmethod
    def _excluded(ev: EvidenceReference, reason: str) -> ExcludedEvidence:
        return ExcludedEvidence(
            evidence_id=ev.evidence_id,
            fact_type=ev.fact_type,
            value=_value_of(ev),
            reason=reason,
        )

    @staticmethod
    def filter_evidence(
        evidence: list[EvidenceReference],
        approved_set: ApprovedEvidenceSet,
    ) -> list[EvidenceReference]:
        """Return only the evidence references included by the approved set."""
        included = set(approved_set.included_ids)
        return [e for e in evidence if e.evidence_id in included]
