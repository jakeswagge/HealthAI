"""Pydantic models for payer-specific guideline packs (Final Milestone).

A :class:`PayerProfile` describes a payer (insurer) and which guideline pack +
version its reviews/appeals should use. Profiles are stored as local JSON under
``data/payers/`` and loaded by :class:`app.payers.repository.PayerRepository`.

All packs use SIMPLIFIED MOCK policies. No proprietary payer content is bundled.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

#: Canonical guideline-pack identifiers supported by the platform.
KNOWN_PACKS = (
    "DEFAULT",
    "AETNA",
    "UNITEDHEALTHCARE",
    "CIGNA",
    "HUMANA",
    "MOCK_PAYER",
)


class PayerStatus(str, Enum):
    """Lifecycle status of a payer profile."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DRAFT = "DRAFT"


class PayerProfile(BaseModel):
    """A payer and the guideline pack/version its decisions use."""

    payer_id: str = Field(..., description="Stable payer identifier, e.g. 'AETNA'.")
    payer_name: str = Field(..., description="Human-readable payer name.")
    guideline_pack: str = Field(
        default="DEFAULT",
        description="Guideline-pack id this payer maps to.",
    )
    version: str = Field(default="1.0", description="Pack version string.")
    effective_date: Optional[str] = Field(
        default=None, description="ISO-8601 date the pack becomes effective."
    )
    status: PayerStatus = Field(default=PayerStatus.ACTIVE)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v):
        if isinstance(v, PayerStatus):
            return v
        if v is None:
            return PayerStatus.ACTIVE
        return PayerStatus(str(v).strip().upper())

    @field_validator("effective_date", mode="before")
    @classmethod
    def _coerce_date(cls, v):
        if v is None:
            return None
        if isinstance(v, date):
            return v.isoformat()
        s = str(v).strip()
        return s or None

    @property
    def is_active(self) -> bool:
        return self.status is PayerStatus.ACTIVE
