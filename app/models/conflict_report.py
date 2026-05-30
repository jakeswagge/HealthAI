"""Pydantic models for cross-document conflict detection.

When a case contains multiple documents, the same logical fact (diagnosis,
member id, requested service, denial reason, ...) may appear with different
values. A :class:`ConflictReport` captures those disagreements with a severity
level so reviewers can resolve them before relying on the case.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ConflictSeverity(str, Enum):
    """How serious a detected conflict is."""

    HIGH = "HIGH"      # identity / safety critical (member id, diagnosis)
    MEDIUM = "MEDIUM"  # clinically significant (requested service, denial reason)
    LOW = "LOW"        # minor / informational


class FactConflict(BaseModel):
    """A single conflicting fact across documents."""

    fact_type: str = Field(..., description="Logical field, e.g. 'diagnosis'.")
    severity: ConflictSeverity = Field(...)
    values: list[str] = Field(
        default_factory=list, description="The distinct conflicting values."
    )
    evidence_ids: list[str] = Field(
        default_factory=list, description="Evidence references involved."
    )
    description: str = Field(default="")
    # Stable id so human resolutions can reference a conflict across reruns.
    # Deterministic per (case_id, fact_type); populated by the assembly engine.
    conflict_id: str = Field(default="")


class ConflictReport(BaseModel):
    """All conflicts detected during case assembly."""

    case_id: str = Field(...)
    conflicts: list[FactConflict] = Field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0

    def by_severity(self, severity: ConflictSeverity) -> list[FactConflict]:
        return [c for c in self.conflicts if c.severity is severity]

    @property
    def highest_severity(self) -> ConflictSeverity | None:
        for sev in (ConflictSeverity.HIGH, ConflictSeverity.MEDIUM, ConflictSeverity.LOW):
            if self.by_severity(sev):
                return sev
        return None
