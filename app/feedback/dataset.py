"""FeedbackDataset: aggregate structured learning data for export.

Collects reviewer corrections, conflict resolutions, and appeal feedback into a
single serializable dataset. This is a DATA COLLECTION artifact only - it is
never used to retrain a model and contains no ML. It exists so that human
corrections can later inform prompt/guideline improvements offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.feedback.repository import ReviewerFeedbackRepository
from app.models.reviewer_feedback import FeedbackTarget
from app.resolution.repository import (
    AuthoritativeFactRepository,
    ConflictResolutionRepository,
)


@dataclass
class FeedbackDataset:
    """Builds an exportable learning dataset from the local stores."""

    feedback_repo: ReviewerFeedbackRepository
    resolution_repo: ConflictResolutionRepository
    facts_repo: AuthoritativeFactRepository

    def build_for_case(self, case_id: str) -> dict:
        """Aggregate all learning data for a single case."""
        feedback = self.feedback_repo.for_case(case_id)
        resolutions = self.resolution_repo.for_case(case_id)
        facts = self.facts_repo.for_case(case_id)

        return {
            "case_id": case_id,
            "reviewer_corrections": [f.model_dump(mode="json") for f in feedback],
            "conflict_resolutions": [r.model_dump(mode="json") for r in resolutions],
            "authoritative_facts": [a.model_dump(mode="json") for a in facts],
            "appeal_feedback": [
                f.model_dump(mode="json")
                for f in feedback
                if f.target_type is FeedbackTarget.APPEAL
            ],
            "summary": {
                "feedback_count": len(feedback),
                "resolution_count": len(resolutions),
                "authoritative_fact_count": len(facts),
            },
        }

    def build_global(self, case_ids: list[str]) -> dict:
        """Aggregate learning data across multiple cases."""
        cases = [self.build_for_case(cid) for cid in case_ids]
        return {
            "cases": cases,
            "totals": {
                "cases": len(cases),
                "reviewer_corrections": sum(len(c["reviewer_corrections"]) for c in cases),
                "conflict_resolutions": sum(len(c["conflict_resolutions"]) for c in cases),
            },
        }

    def export_json(self, case_id: str, indent: int = 2) -> str:
        """Return the per-case learning dataset as JSON text."""
        return json.dumps(self.build_for_case(case_id), indent=indent)
