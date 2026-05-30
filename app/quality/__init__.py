"""Evidence quality scoring.

The :class:`EvidenceQualityEngine` evaluates a set of
:class:`EvidenceReference` objects (typically a case's full inventory) and
produces an :class:`EvidenceQualityAssessment` per reference, scoring
completeness, relevance, consistency, and traceability, and flagging issues
(weak evidence, duplicates, conflicting support, missing support, and
unsupported appeal statements).

Deterministic and offline; independent of extraction/review/appeals/audit.
"""

from app.quality.engine import EvidenceQualityEngine
from app.quality.repository import EvidenceQualityRepository
from app.quality.decision_repository import EvidenceReviewDecisionRepository
from app.quality.workbench import ReviewerWorkbench, EvidenceView

__all__ = [
    "EvidenceQualityEngine",
    "EvidenceQualityRepository",
    "EvidenceReviewDecisionRepository",
    "ReviewerWorkbench",
    "EvidenceView",
]
