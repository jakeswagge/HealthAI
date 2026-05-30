"""Pydantic models for human conflict resolution and authoritative facts.

When the assembly engine detects conflicting values for a fact across
documents, a human reviewer resolves it: they choose the authoritative value,
the rejected alternatives are preserved, and the decision is recorded with a
justification. Nothing is overwritten - the original evidence and the rejected
values are always retained for audit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_resolution_id() -> str:
    return f"RES-{uuid.uuid4().hex[:12].upper()}"


def new_fact_id() -> str:
    return f"FACT-{uuid.uuid4().hex[:12].upper()}"


class ResolutionSource(str, Enum):
    """Who established an authoritative fact."""

    SYSTEM = "SYSTEM"  # auto-resolved by assembly (no conflict)
    HUMAN = "HUMAN"    # established by a human conflict resolution


class ConflictResolution(BaseModel):
    """A recorded human decision resolving a detected conflict."""

    resolution_id: str = Field(default_factory=new_resolution_id)
    case_id: str = Field(...)
    conflict_id: str = Field(..., description="The FactConflict.conflict_id resolved.")
    fact_type: str = Field(default="", description="Logical fact resolved.")
    chosen_value: str = Field(..., description="The authoritative value selected.")
    rejected_values: list[str] = Field(
        default_factory=list, description="Preserved rejected alternatives."
    )
    reviewer_name: str = Field(...)
    justification: str = Field(default="")
    timestamp: str = Field(default_factory=_utc_now_iso)

    @field_validator("rejected_values", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(x).strip() for x in v if str(x).strip()]

    @field_validator("justification", mode="before")
    @classmethod
    def _coerce_just(cls, v):
        return "" if v is None else str(v)


class AuthoritativeFact(BaseModel):
    """The authoritative value for a fact, with provenance.

    Produced either automatically by assembly when there is no conflict
    (``resolution_source = SYSTEM``) or by a human conflict resolution
    (``resolution_source = HUMAN``). Once a HUMAN authoritative fact exists, the
    review/appeal engines use it instead of the auto-resolved value.
    """

    fact_id: str = Field(default_factory=new_fact_id)
    case_id: str = Field(...)
    fact_type: str = Field(...)
    value: str = Field(...)
    source_document: Optional[str] = Field(default=None)
    source_page: Optional[int] = Field(default=None)
    resolution_source: ResolutionSource = Field(default=ResolutionSource.SYSTEM)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    resolution_id: Optional[str] = Field(
        default=None, description="The ConflictResolution that set this (if HUMAN)."
    )
    updated_at: str = Field(default_factory=_utc_now_iso)

    @field_validator("resolution_source", mode="before")
    @classmethod
    def _coerce_source(cls, v):
        if isinstance(v, ResolutionSource):
            return v
        if v is None:
            return ResolutionSource.SYSTEM
        return ResolutionSource(str(v).strip().upper())

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_conf(cls, v):
        if v is None:
            return 0.0
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))
