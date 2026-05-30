"""Evidence governance.

Lets an organization operate HealthAI on reviewer-approved evidence only
(validated mode), with configurable quality thresholds and export gates.

- :class:`GovernanceSettingsRepository` persists the org-level policy.
- :class:`ValidatedEvidenceEngine` applies the policy to a case's evidence,
  producing an :class:`ApprovedEvidenceSet` (included vs. excluded + reasons).
- :class:`GovernanceComplianceChecker` detects policy violations.

Reviewer authority always wins: rejected evidence is never included in
validated mode. Independent of extraction/review/appeals.
"""

from app.governance.repository import GovernanceSettingsRepository
from app.governance.engine import ValidatedEvidenceEngine
from app.governance.compliance import GovernanceComplianceChecker

__all__ = [
    "GovernanceSettingsRepository",
    "ValidatedEvidenceEngine",
    "GovernanceComplianceChecker",
]
