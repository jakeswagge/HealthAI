"""Pydantic models for clinical guidelines.

A :class:`ClinicalGuideline` encodes the (simplified) medical-necessity
criteria a payer uses to decide whether a requested service is covered. The
guideline library is stored as local JSON (no database yet) and loaded into
these typed models.

Criteria are expressed with keyword sets so the deterministic review engine can
evaluate them against the limited structured evidence in a ``PatientCase``,
while a Claude-backed agent can use the same descriptions for richer reasoning.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class GuidelineCriterion(BaseModel):
    """A single required medical-necessity criterion."""

    id: str = Field(..., description="Stable identifier for the criterion.")
    description: str = Field(..., description="Human-readable criterion text.")
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Evidence keywords. Presence in the case evidence marks the "
            "criterion MET; reference in a denial reason marks it UNMET."
        ),
    )
    required: bool = Field(
        default=True, description="Whether the criterion is mandatory."
    )


class Contraindication(BaseModel):
    """A condition that, if present, justifies denial."""

    id: str = Field(..., description="Stable identifier for the contraindication.")
    description: str = Field(..., description="Human-readable description.")
    keywords: list[str] = Field(
        default_factory=list,
        description="Keywords whose presence indicates the contraindication.",
    )


class ClinicalGuideline(BaseModel):
    """Medical-necessity criteria for a single service/drug.

    The first eight fields are the core schema required by Milestone 3. The
    remaining optional fields (``aliases``, ``applicable_icd10``,
    ``applicable_cpt``) are matching aids used to map a ``PatientCase`` to the
    correct guideline; they do not change the clinical content.
    """

    guideline_id: str = Field(..., description="Unique guideline identifier.")
    service_name: str = Field(..., description="Service or drug name.")
    diagnosis: str = Field(
        ..., description="Target diagnosis / indication this guideline covers."
    )
    required_criteria: list[GuidelineCriterion] = Field(
        default_factory=list, description="Criteria that must be met."
    )
    contraindications: list[Contraindication] = Field(
        default_factory=list, description="Conditions that justify denial."
    )
    supporting_evidence: list[str] = Field(
        default_factory=list,
        description="Citations/references backing the criteria.",
    )
    version: str = Field(..., description="Guideline version string.")
    source: str = Field(..., description="Issuing authority / source.")

    # --- Optional matching aids (not part of the core clinical content) --- #
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternate service/drug names used for matching.",
    )
    applicable_icd10: list[str] = Field(
        default_factory=list,
        description="ICD-10 code prefixes this guideline applies to.",
    )
    applicable_cpt: list[str] = Field(
        default_factory=list,
        description="CPT/HCPCS codes this guideline applies to.",
    )

    def required_count(self) -> int:
        """Number of required criteria."""
        return sum(1 for c in self.required_criteria if c.required)

    def find_criterion(self, criterion_id: str) -> Optional[GuidelineCriterion]:
        """Return a criterion by id, or None."""
        for c in self.required_criteria:
            if c.id == criterion_id:
                return c
        return None
