"""AppealService: attach generated appeals + the export-mark hook.

Extracted from ``CaseService`` during the Milestone 12 facade decomposition.
Owns the appeal-attachment transition (which automatically queues the case for
human review) and the ``mark_exported`` audit hook used after an export package
is produced.

Behavior is identical to the original CaseService methods - a cohesion
extraction only.
"""

from __future__ import annotations

from app.audit.repository import AuditRepository
from app.cases.lifecycle import CaseLifecycle
from app.governance.safety import SafetyGate
from app.models.appeal_letter import AppealLetter
from app.models.audit_event import AuditActor, AuditEventType
from app.models.case_record import CaseRecord, CaseStatus
from app.models.governance import GovernanceComplianceReport, GovernanceSettings


class ExportBlockedError(Exception):
    """Raised when pilot safety policy blocks export."""


class AppealService:
    """Attach appeal letters and record export generation."""

    def __init__(
        self,
        lifecycle: CaseLifecycle,
        audit: AuditRepository,
        settings_provider=None,
    ) -> None:
        self.lifecycle = lifecycle
        self.audit = audit
        self.settings_provider = settings_provider

    def _settings(self) -> GovernanceSettings:
        if self.settings_provider is None:
            return GovernanceSettings()
        return self.settings_provider()

    def attach_appeal(self, case_id: str, appeal: AppealLetter) -> CaseRecord:
        """Attach appeal output, move to APPEAL_GENERATED then PENDING review."""
        record = self.lifecycle.require(case_id)
        gate = SafetyGate(self._settings()).appeal(appeal)
        appeal.safety_gate = gate.model_dump(mode="json")
        record.appeal_letter = appeal
        if record.status is not CaseStatus.PENDING_HUMAN_REVIEW:
            self.lifecycle.set_status(record, CaseStatus.APPEAL_GENERATED)
        self.audit.log(
            case_id,
            AuditEventType.APPEAL_GENERATED,
            details=f"Appeal generated: {appeal.appeal_id}; safety={gate.status.value}.",
        )
        # Appeals automatically enter the human-review queue.
        if record.status is CaseStatus.APPEAL_GENERATED:
            self.lifecycle.set_status(record, CaseStatus.PENDING_HUMAN_REVIEW)
        return self.lifecycle.save(record)

    def mark_exported(
        self,
        case_id: str,
        settings: GovernanceSettings | None = None,
        compliance: GovernanceComplianceReport | None = None,
    ) -> CaseRecord:
        """Record that a case's export package was generated."""
        record = self.lifecycle.require(case_id)
        if settings is not None and compliance is not None:
            gate = SafetyGate(settings).export(record, compliance)
            if gate.blocked:
                raise ExportBlockedError("; ".join(gate.reasons))
        self.audit.log(
            case_id,
            AuditEventType.CASE_EXPORTED,
            details="Export package generated.",
            actor=AuditActor.USER,
        )
        return self.lifecycle.save(record)
