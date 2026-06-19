"""ExplainabilityService: governance-enforced reviews/appeals + explanations.

Milestone 13. This service closes the trust gap: in VALIDATED mode the review
and appeal are generated from ONLY the governance-approved evidence, and the
resulting explanations prove which evidence was used vs. excluded.

Flow
----
1. Ask governance for the case's :class:`ApprovedEvidenceSet` (draft -> all
   evidence; validated -> approved-only, quality-gated, rejected-never).
2. Synthesize a :class:`PatientCase` from ONLY the permitted evidence (via the
   assembly engine's ``synthesize_from_evidence``), so rejected/excluded
   evidence cannot influence the case the agents see.
3. Run the review agent / appeal agent on that constrained case.
4. Build the :class:`ReviewExplanation` / :class:`AppealExplanation` /
   :class:`TraceabilityChain` from the evidence split.

The agents are injected (defaulting to the offline-capable agents), so this
works fully offline and in tests. Behavior in DRAFT mode is equivalent to the
existing pipeline (all evidence permitted).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.assembly.engine import CaseAssemblyEngine
from app.audit.repository import AuditRepository
from app.cases.document_repository import CaseDocumentRepository
from app.cases.governance_service import GovernanceService
from app.cases.lifecycle import CaseLifecycle
from app.evidence.repository import EvidenceRepository
from app.evidence.linker import link_review
from app.explainability.engine import ExplainabilityEngine
from app.quality.decision_repository import EvidenceReviewDecisionRepository
from app.quality.repository import EvidenceQualityRepository
from app.appeals.appeal_agent import AppealGenerationAgent
from app.appeals.verifier import AppealVerifier
from app.governance.safety import SafetyGate
from app.review.review_agent import GuidelineReviewAgent
from app.models.appeal_letter import AppealLetter
from app.models.audit_event import AuditEventType
from app.models.explanation import (
    AppealExplanation,
    ReviewExplanation,
    TraceabilityChain,
)
from app.models.governance import ApprovedEvidenceSet, GovernanceSettings
from app.models.patient_case import PatientCase
from app.models.review_result import ReviewResult


@dataclass
class GovernedReview:
    """A governance-enforced review plus its explanation + evidence set."""

    review: ReviewResult
    explanation: ReviewExplanation
    approved_set: ApprovedEvidenceSet
    patient_case: PatientCase
    used_ai: bool


@dataclass
class GovernedAppeal:
    """A governance-enforced appeal plus its explanation + evidence set."""

    appeal: AppealLetter
    explanation: AppealExplanation
    approved_set: ApprovedEvidenceSet
    review: ReviewResult
    patient_case: PatientCase
    used_ai: bool


class ExplainabilityService:
    """Generate governance-constrained reviews/appeals with explanations."""

    def __init__(
        self,
        lifecycle: CaseLifecycle,
        documents: CaseDocumentRepository,
        evidence: EvidenceRepository,
        evidence_quality: EvidenceQualityRepository,
        evidence_decisions: EvidenceReviewDecisionRepository,
        assembly: CaseAssemblyEngine,
        governance: GovernanceService,
        audit: AuditRepository,
        explainability: ExplainabilityEngine | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.documents = documents
        self.evidence = evidence
        self.evidence_quality = evidence_quality
        self.evidence_decisions = evidence_decisions
        self.assembly = assembly
        self.governance = governance
        self.audit = audit
        self.explainability = explainability or ExplainabilityEngine()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _decisions_by_evidence(self, case_id: str) -> dict:
        latest: dict = {}
        for d in self.evidence_decisions.for_case(case_id):
            latest[d.evidence_id] = d  # ordered ASC -> last wins
        return latest

    def _quality_by_id(self, case_id: str) -> dict:
        return {q.evidence_id: q for q in self.evidence_quality.for_case(case_id)}

    def _permitted_case(
        self,
        case_id: str,
        approved_set: ApprovedEvidenceSet,
        all_evidence: list,
    ) -> PatientCase:
        """Synthesize a PatientCase from ONLY the permitted evidence."""
        included = {e for e in approved_set.included_ids}
        permitted = [e for e in all_evidence if e.evidence_id in included]
        documents = self.documents.for_case(case_id)
        context = self.assembly.synthesize_from_evidence(
            case_id, permitted, documents
        )
        return context.patient_case

    def _permitted_context(
        self,
        case_id: str,
        approved_set: ApprovedEvidenceSet,
        all_evidence: list,
    ):
        included = {e for e in approved_set.included_ids}
        permitted = [e for e in all_evidence if e.evidence_id in included]
        return self.assembly.synthesize_from_evidence(
            case_id,
            permitted,
            self.documents.for_case(case_id),
        )

    # ------------------------------------------------------------------ #
    # Governance-enforced review
    # ------------------------------------------------------------------ #
    def generate_review(
        self,
        case_id: str,
        settings: GovernanceSettings | None = None,
        review_agent: GuidelineReviewAgent | None = None,
    ) -> GovernedReview:
        """Generate a review constrained to governance-approved evidence."""
        self.lifecycle.require(case_id)
        all_evidence = self.evidence.for_case(case_id)
        approved_set = self.governance.build_approved_evidence_set(case_id, settings)
        permitted_context = self._permitted_context(case_id, approved_set, all_evidence)
        patient_case = permitted_context.patient_case

        agent = review_agent or GuidelineReviewAgent()
        agent_result = agent.review(patient_case)
        review = link_review(agent_result.result, permitted_context)

        explanation = self.explainability.explain_review(
            case_id,
            review,
            all_evidence,
            approved_set,
            decisions_by_evidence=self._decisions_by_evidence(case_id),
            quality_by_id=self._quality_by_id(case_id),
        )

        self.audit.log(
            case_id,
            AuditEventType.REVIEW_EXPLANATION_GENERATED,
            details=(
                f"Mode={approved_set.mode.value}: review {review.recommendation.value} "
                f"using {len(explanation.evidence_used)} evidence reference(s), "
                f"{len(explanation.evidence_excluded)} excluded."
            ),
        )
        return GovernedReview(
            review=review,
            explanation=explanation,
            approved_set=approved_set,
            patient_case=patient_case,
            used_ai=agent_result.used_ai,
        )

    # ------------------------------------------------------------------ #
    # Governance-enforced appeal
    # ------------------------------------------------------------------ #
    def generate_appeal(
        self,
        case_id: str,
        settings: GovernanceSettings | None = None,
        review_agent: GuidelineReviewAgent | None = None,
        appeal_agent: AppealGenerationAgent | None = None,
    ) -> GovernedAppeal:
        """Generate an appeal constrained to governance-approved evidence."""
        governed_review = self.generate_review(case_id, settings, review_agent)

        agent = appeal_agent or AppealGenerationAgent()
        agent_result = agent.generate(
            governed_review.patient_case, governed_review.review
        )
        appeal = agent_result.appeal
        appeal.drafted_by_ai = agent_result.used_ai
        appeal.draft_backend = agent_result.backend
        appeal.draft_model = agent_result.model

        all_evidence = self.evidence.for_case(case_id)
        verifier_context = self.assembly.synthesize_from_evidence(
            case_id,
            [
                ev for ev in all_evidence
                if ev.evidence_id in set(governed_review.approved_set.included_ids)
            ],
            self.documents.for_case(case_id),
        )
        appeal = AppealVerifier().verify(appeal, verifier_context)
        gate = SafetyGate(settings or self.governance.get_governance_settings()).appeal(appeal)
        appeal.safety_gate = gate.model_dump(mode="json")
        explanation = self.explainability.explain_appeal(
            case_id,
            appeal,
            all_evidence,
            governed_review.approved_set,
            decisions_by_evidence=self._decisions_by_evidence(case_id),
            quality_by_id=self._quality_by_id(case_id),
        )

        self.audit.log(
            case_id,
            AuditEventType.APPEAL_EXPLANATION_GENERATED,
            details=(
                f"Mode={governed_review.approved_set.mode.value}: appeal "
                f"{appeal.appeal_id} using {len(explanation.evidence_used)} "
                f"evidence reference(s), {len(explanation.evidence_excluded)} excluded."
            ),
        )
        return GovernedAppeal(
            appeal=appeal,
            explanation=explanation,
            approved_set=governed_review.approved_set,
            review=governed_review.review,
            patient_case=governed_review.patient_case,
            used_ai=agent_result.used_ai,
        )

    # ------------------------------------------------------------------ #
    # Traceability chain
    # ------------------------------------------------------------------ #
    def traceability_chain(
        self,
        case_id: str,
        settings: GovernanceSettings | None = None,
    ) -> TraceabilityChain:
        """Build the full evidence-lineage chain for a case."""
        self.lifecycle.require(case_id)
        all_evidence = self.evidence.for_case(case_id)
        approved_set = self.governance.build_approved_evidence_set(case_id, settings)
        return self.explainability.build_traceability_chain(
            case_id,
            all_evidence,
            approved_set,
            decisions_by_evidence=self._decisions_by_evidence(case_id),
            quality_by_id=self._quality_by_id(case_id),
        )
