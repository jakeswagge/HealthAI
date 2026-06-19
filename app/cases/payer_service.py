"""PayerService: payer-specific guideline-pack reviews + appeals.

Final Milestone. Wires payer selection into the existing review/appeal flow:

    Case -> Payer Selection -> Guideline Pack -> Review/Appeal

It reuses the governance-enforced, explainable pipeline so payer packs compose
with everything from earlier milestones (validated evidence, explanations,
traceability). The only addition is which guideline library the review agent
matches against, plus payer/pack/version provenance stamped on the results.

No proprietary payer content is used; packs are simplified mock policies.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.cases.explainability_service import (
    ExplainabilityService,
    GovernedAppeal,
    GovernedReview,
)
from app.cases.lifecycle import CaseLifecycle
from app.payers.packs import GuidelinePackResolver, get_pack_resolver
from app.payers.repository import PayerRepository, get_payer_repository
from app.review.review_agent import GuidelineReviewAgent
from app.appeals.appeal_agent import AppealGenerationAgent
from app.models.appeal_letter import AppealLetter
from app.models.governance import GovernanceSettings
from app.models.payer import PayerProfile
from app.models.review_result import ReviewResult
from app.policies.formulary import FormularyPolicyIndex


@dataclass
class PayerReview:
    """A payer-pack-aware review result + its governance explanation."""

    payer: PayerProfile
    governed: GovernedReview

    @property
    def review(self) -> ReviewResult:
        return self.governed.review


@dataclass
class PayerAppeal:
    """A payer-pack-aware appeal + its governance explanation."""

    payer: PayerProfile
    governed: GovernedAppeal

    @property
    def appeal(self) -> AppealLetter:
        return self.governed.appeal


class PayerService:
    """Run reviews/appeals under a selected payer's guideline pack."""

    def __init__(
        self,
        lifecycle: CaseLifecycle,
        explainability: ExplainabilityService,
        payers: PayerRepository | None = None,
        pack_resolver: GuidelinePackResolver | None = None,
        formulary_policy: FormularyPolicyIndex | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.explainability = explainability
        self.payers = payers or get_payer_repository()
        self.pack_resolver = pack_resolver or get_pack_resolver()
        self.formulary_policy = formulary_policy

    # ------------------------------------------------------------------ #
    # Payer catalog
    # ------------------------------------------------------------------ #
    def list_payers(self) -> list[PayerProfile]:
        return self.payers.all()

    def get_payer(self, payer_id: str | None) -> PayerProfile:
        return self.payers.get_or_default(payer_id)

    def available_packs(self) -> list[str]:
        return self.pack_resolver.available_packs()

    # ------------------------------------------------------------------ #
    # Provenance stamping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _stamp_review(review: ReviewResult, payer: PayerProfile, version: str) -> None:
        review.payer_id = payer.payer_id
        review.guideline_pack = payer.guideline_pack
        review.guideline_version = version

    @staticmethod
    def _stamp_appeal(appeal: AppealLetter, payer: PayerProfile, version: str) -> None:
        appeal.payer_id = payer.payer_id
        appeal.guideline_pack = payer.guideline_pack
        appeal.guideline_version = version

    def _version_for(self, payer: PayerProfile, review: ReviewResult) -> str:
        """Prefer the matched guideline's version; fall back to the pack version."""
        repo = self.pack_resolver.resolve(payer.guideline_pack)
        if review.guideline_id:
            g = repo.get(review.guideline_id)
            if g is not None:
                return g.version
        return payer.version

    def _review_agent_for(self, payer: PayerProfile) -> GuidelineReviewAgent:
        repo = self.pack_resolver.resolve(payer.guideline_pack)
        return GuidelineReviewAgent(
            repository=repo,
            formulary_policy=self.formulary_policy,
            payer_id=payer.payer_id,
        )

    # ------------------------------------------------------------------ #
    # Reviews / appeals
    # ------------------------------------------------------------------ #
    def review_with_payer(
        self,
        case_id: str,
        payer_id: str | None = None,
        settings: GovernanceSettings | None = None,
    ) -> PayerReview:
        """Generate a governed review using the payer's guideline pack."""
        self.lifecycle.require(case_id)
        payer = self.get_payer(payer_id)
        agent = self._review_agent_for(payer)

        governed = self.explainability.generate_review(
            case_id, settings, review_agent=agent
        )
        version = self._version_for(payer, governed.review)
        self._stamp_review(governed.review, payer, version)
        return PayerReview(payer=payer, governed=governed)

    def appeal_with_payer(
        self,
        case_id: str,
        payer_id: str | None = None,
        settings: GovernanceSettings | None = None,
    ) -> PayerAppeal:
        """Generate a governed appeal using the payer's guideline pack."""
        self.lifecycle.require(case_id)
        payer = self.get_payer(payer_id)
        repo = self.pack_resolver.resolve(payer.guideline_pack)
        review_agent = self._review_agent_for(payer)
        appeal_agent = AppealGenerationAgent(repository=repo)

        governed = self.explainability.generate_appeal(
            case_id,
            settings,
            review_agent=review_agent,
            appeal_agent=appeal_agent,
        )
        version = self._version_for(payer, governed.review)
        self._stamp_review(governed.review, payer, version)
        self._stamp_appeal(governed.appeal, payer, version)
        return PayerAppeal(payer=payer, governed=governed)
