"""ConflictResolutionEngine: apply human decisions, derive authoritative facts.

Responsibilities:
- Seed SYSTEM authoritative facts from an assembled :class:`UnifiedCaseContext`
  (the auto-resolved values), without overriding any HUMAN facts.
- Apply a reviewer's :class:`ConflictResolution`: record it (append-only),
  preserve the rejected values, upsert a HUMAN :class:`AuthoritativeFact`, and
  emit audit events.
- Apply authoritative facts onto a :class:`PatientCase` / :class:`UnifiedCaseContext`
  so review + appeal use the human-chosen values.

No automatic conflict resolution: ``resolve`` only runs in response to an
explicit human decision. Original evidence and rejected values are never
destroyed.
"""

from __future__ import annotations

from app.audit.repository import AuditRepository
from app.models.audit_event import AuditActor, AuditEventType
from app.models.conflict_resolution import (
    AuthoritativeFact,
    ConflictResolution,
    ResolutionSource,
)
from app.models.patient_case import Decision, FieldSource, PatientCase
from app.models.unified_case_context import UnifiedCaseContext
from app.resolution.repository import (
    AuthoritativeFactRepository,
    ConflictResolutionRepository,
)

# PatientCase scalar attributes that an authoritative fact can override.
_SCALAR_CASE_FIELDS = {
    "patient_name",
    "member_id",
    "date_of_birth",
    "diagnosis",
    "requested_service",
    "insurance_company",
    "physician_name",
    "denial_reason",
}


class ConflictResolutionEngine:
    """Apply reviewer conflict resolutions and manage authoritative facts."""

    def __init__(
        self,
        resolutions: ConflictResolutionRepository,
        facts: AuthoritativeFactRepository,
        audit: AuditRepository,
    ) -> None:
        self.resolutions = resolutions
        self.facts = facts
        self.audit = audit

    # ------------------------------------------------------------------ #
    # Seeding (SYSTEM facts)
    # ------------------------------------------------------------------ #
    def seed_system_facts(self, context: UnifiedCaseContext) -> list[AuthoritativeFact]:
        """Create SYSTEM authoritative facts from auto-resolved values.

        Never overrides an existing HUMAN fact. Returns the facts that were
        written or already present.
        """
        out: list[AuthoritativeFact] = []
        for fact_type, rf in context.resolved_facts.items():
            existing = self.facts.get(context.case_id, fact_type)
            if existing is not None and existing.resolution_source is ResolutionSource.HUMAN:
                out.append(existing)
                continue
            fact = AuthoritativeFact(
                case_id=context.case_id,
                fact_type=fact_type,
                value=rf.value,
                source_document=rf.source_filename,
                source_page=rf.source_page,
                resolution_source=ResolutionSource.SYSTEM,
                confidence=0.6,
            )
            self.facts.upsert(fact)
            out.append(fact)
        return out

    # ------------------------------------------------------------------ #
    # Human resolution
    # ------------------------------------------------------------------ #
    def resolve(
        self,
        case_id: str,
        conflict_id: str,
        fact_type: str,
        chosen_value: str,
        rejected_values: list[str],
        reviewer_name: str,
        justification: str = "",
        source_document: str | None = None,
        source_page: int | None = None,
    ) -> tuple[ConflictResolution, AuthoritativeFact]:
        """Record a human conflict resolution and update the authoritative fact.

        Preserves rejected values, writes an append-only resolution, upserts a
        HUMAN authoritative fact, and records two audit events.
        """
        if not chosen_value or not chosen_value.strip():
            raise ValueError("chosen_value is required for a conflict resolution.")
        if not reviewer_name or not reviewer_name.strip():
            raise ValueError("reviewer_name is required for a conflict resolution.")

        resolution = ConflictResolution(
            case_id=case_id,
            conflict_id=conflict_id,
            fact_type=fact_type,
            chosen_value=chosen_value.strip(),
            rejected_values=rejected_values,
            reviewer_name=reviewer_name.strip(),
            justification=justification,
        )
        self.resolutions.add(resolution)

        fact = AuthoritativeFact(
            case_id=case_id,
            fact_type=fact_type,
            value=chosen_value.strip(),
            source_document=source_document,
            source_page=source_page,
            resolution_source=ResolutionSource.HUMAN,
            confidence=0.99,
            resolution_id=resolution.resolution_id,
        )
        self.facts.upsert(fact)

        # Audit: the resolution and the resulting authoritative fact update.
        self.audit.log(
            case_id,
            AuditEventType.CONFLICT_RESOLVED,
            details=(
                f"{reviewer_name} resolved '{fact_type}' (conflict {conflict_id}): "
                f"chose '{chosen_value}'; rejected {rejected_values}. "
                f"Justification: {justification or '(none)'}"
            ),
            actor=AuditActor.USER,
        )
        self.audit.log(
            case_id,
            AuditEventType.AUTHORITATIVE_FACT_UPDATED,
            details=f"Authoritative '{fact_type}' set to '{chosen_value}' (HUMAN).",
            actor=AuditActor.USER,
        )
        return resolution, fact

    # ------------------------------------------------------------------ #
    # Applying authoritative facts
    # ------------------------------------------------------------------ #
    def apply_to_case(self, case: PatientCase, case_id: str) -> PatientCase:
        """Return a copy of ``case`` with authoritative facts applied.

        Only scalar fields are overridden (code lists keep their assembled
        union). Each overridden field gets a HUMAN/ SYSTEM-sourced FieldSource.
        """
        facts = self.facts.for_case(case_id)
        if not facts:
            return case

        data = case.model_dump()
        field_sources = dict(case.field_sources)

        for fact in facts:
            if fact.fact_type in _SCALAR_CASE_FIELDS:
                data[fact.fact_type] = fact.value
                field_sources[fact.fact_type] = FieldSource(
                    source_document=fact.source_document,
                    source_page=fact.source_page,
                    evidence_id=None,
                    quoted_text=None,
                )
            elif fact.fact_type == "decision":
                data["decision"] = fact.value

        data["field_sources"] = {
            k: (v.model_dump() if isinstance(v, FieldSource) else v)
            for k, v in field_sources.items()
        }
        updated = PatientCase.model_validate(data)
        return updated

    def apply_to_context(self, context: UnifiedCaseContext) -> UnifiedCaseContext:
        """Return a copy of the context with authoritative facts applied to its case."""
        updated_case = self.apply_to_case(context.patient_case, context.case_id)
        return context.model_copy(update={"patient_case": updated_case})
