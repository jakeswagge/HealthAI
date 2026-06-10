"""Pydantic model for the assembled, evidence-backed view of a multi-doc case.

A :class:`UnifiedCaseContext` is the output of the :class:`CaseAssemblyEngine`.
It merges evidence from every :class:`CaseDocument` in a case into:

- a de-duplicated evidence inventory,
- a "best value" per logical fact (with its supporting evidence id),
- a :class:`ConflictReport`,
- a list of missing/required information,
- a synthesized :class:`PatientCase` (so downstream review/appeal still work).

It is the bridge between the multi-document world and the single-`PatientCase`
contract the earlier milestones depend on - preserving backward compatibility.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.conflict_report import ConflictReport
from app.models.evidence_reference import EvidenceReference
from app.models.patient_case import PatientCase


class ResolvedFact(BaseModel):
    """The chosen value for a logical fact plus its supporting evidence."""

    fact_type: str
    value: str
    evidence_id: Optional[str] = None
    source_filename: Optional[str] = None
    source_page: Optional[int] = None
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    alternatives: list[str] = Field(default_factory=list)


class UnifiedCaseContext(BaseModel):
    """The assembled, traceable view of a multi-document case."""

    case_id: str
    document_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    resolved_facts: dict[str, ResolvedFact] = Field(default_factory=dict)
    conflict_report: ConflictReport
    missing_information: list[str] = Field(default_factory=list)
    patient_case: PatientCase

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def evidence_for_fact(self, fact_type: str) -> list[EvidenceReference]:
        """All evidence references for a given logical fact."""
        return [e for e in self.evidence if e.fact_type == fact_type]

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceReference]:
        for e in self.evidence:
            if e.evidence_id == evidence_id:
                return e
        return None

    def inventory(self) -> list[dict]:
        """A serializable evidence inventory for export/UI."""
        return [
            {
                "evidence_id": e.evidence_id,
                "fact_type": e.fact_type,
                "normalized_fact": e.normalized_fact,
                "quoted_text": e.quoted_text,
                "source_document_id": e.source_document_id,
                "source_filename": e.source_filename,
                "page_number": e.page_number,
                "section_label": e.section_label,
                "confidence_score": e.confidence_score,
                "citation": e.citation(),
            }
            for e in self.evidence
        ]
