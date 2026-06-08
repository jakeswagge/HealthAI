"""Structured representation of a prior-authorization case.

This is the validated output of the :class:`MedicalExtractionAgent`. It is the
contract every AI backend must satisfy, which lets us validate, retry, and
score extractions deterministically regardless of which model produced them.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator


class Decision(str, Enum):
    """Normalized determination found in a prior-authorization letter."""

    APPROVED = "approved"
    DENIED = "denied"
    PARTIAL = "partial"
    PENDING = "pending"
    UNKNOWN = "unknown"


# Field names that carry the substantive clinical/administrative content.
# Used to compute a completeness-based confidence fallback.
CORE_FIELDS: tuple[str, ...] = (
    "patient_name",
    "member_id",
    "date_of_birth",
    "diagnosis",
    "icd10_codes",
    "requested_service",
    "cpt_codes",
    "insurance_company",
    "decision",
    "physician_name",
)


class FieldSource(BaseModel):
    """Optional source attribution for a single extracted field (M6/M7).

    Lets a ``PatientCase`` answer "what document/page did this come from?"
    without changing any existing field. All attributes are optional so older
    cases (and the offline backend) remain valid with this left empty.
    """

    source_document: Optional[str] = None  # filename or document id
    source_page: Optional[int] = None
    evidence_id: Optional[str] = None
    quoted_text: Optional[str] = None


class PatientCase(BaseModel):
    """Structured healthcare data extracted from an insurance document."""
    patient_name: Optional[str] = Field(
        default=None, description="Full name of the patient/member."
    )
    member_id: Optional[str] = Field(
        default=None, description="Insurance member/subscriber ID."
    )
    date_of_birth: Optional[str] = Field(
        default=None,
        description="Patient date of birth as written in the document.",
    )
    diagnosis: Optional[str] = Field(
        default=None, description="Primary diagnosis description."
    )
    icd10_codes: list[str] = Field(
        default_factory=list, description="ICD-10 diagnosis codes."
    )
    requested_service: Optional[str] = Field(
        default=None,
        description="Procedure or service requested/authorized.",
    )
    cpt_codes: list[str] = Field(
        default_factory=list, description="CPT/HCPCS procedure codes."
    )
    insurance_company: Optional[str] = Field(
        default=None, description="Name of the payer / insurance company."
    )
    decision: Decision = Field(
        default=Decision.UNKNOWN,
        description="Determination: approved, denied, partial, or unknown.",
    )
    denial_reason: Optional[str] = Field(
        default=None,
        description="Reason for denial. Null for approvals or when absent.",
    )
    physician_name: Optional[str] = Field(
        default=None, description="Requesting/ordering physician name."
    )
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Model/heuristic confidence in the extraction (0.0-1.0).",
    )

    # Optional traceability (Milestone 6/7). Maps a field name to its source
    # document/page/evidence. Backward-compatible: defaults to empty.
    field_sources: dict[str, "FieldSource"] = Field(
        default_factory=dict,
        description="Optional per-field source attribution (field -> FieldSource).",
    )

    # ------------------------------------------------------------------ #
    # Validators / coercion
    # ------------------------------------------------------------------ #
    @field_validator(
        "patient_name",
        "member_id",
        "date_of_birth",
        "diagnosis",
        "requested_service",
        "insurance_company",
        "denial_reason",
        "physician_name",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v):
        """Treat empty strings and common null markers as ``None``."""
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            if stripped == "" or stripped.lower() in {"null", "none", "n/a", "na"}:
                return None
            return stripped
        return v

    @field_validator("icd10_codes", "cpt_codes", mode="before")
    @classmethod
    def _coerce_code_list(cls, v):
        """Accept None / a single string / a list; return a clean list."""
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        cleaned: list[str] = []
        for item in v:
            if item is None:
                continue
            code = str(item).strip().upper()
            if code and code not in {"NULL", "NONE", "N/A", "NA"}:
                cleaned.append(code)
        # De-duplicate while preserving order.
        seen: set[str] = set()
        result: list[str] = []
        for code in cleaned:
            if code not in seen:
                seen.add(code)
                result.append(code)
        return result

    @field_validator("decision", mode="before")
    @classmethod
    def _coerce_decision(cls, v):
        """Map free-form decision text to the :class:`Decision` enum."""
        if v is None:
            return Decision.UNKNOWN
        if isinstance(v, Decision):
            return v
        text = str(v).strip().lower()
        if text in {"", "null", "none", "n/a", "unknown"}:
            return Decision.UNKNOWN
        if any(k in text for k in ("pending", "in review", "under review")):
            return Decision.PENDING
        if "partial" in text:
            return Decision.PARTIAL
        if any(k in text for k in ("approv", "favorable", "authorized", "certif")):
            return Decision.APPROVED
        if any(k in text for k in ("deni", "denial", "adverse", "reject", "not approved")):
            return Decision.DENIED
        return Decision.UNKNOWN

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        """Clamp confidence to [0, 1]; tolerate strings and out-of-range."""
        if v is None:
            return 0.0
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    # ------------------------------------------------------------------ #
    # Derived helpers
    # ------------------------------------------------------------------ #
    @computed_field  # type: ignore[prop-decorator]
    @property
    def completeness(self) -> float:
        """Fraction of core fields that were populated (0.0-1.0)."""
        populated = 0
        for name in CORE_FIELDS:
            value = getattr(self, name)
            if name in {"icd10_codes", "cpt_codes"}:
                if value:
                    populated += 1
            elif name == "decision":
                if value is not Decision.UNKNOWN:
                    populated += 1
            elif value:
                populated += 1
        return round(populated / len(CORE_FIELDS), 4)

    def summary(self) -> str:
        """Human-readable one-paragraph summary of the case."""
        name = self.patient_name or "Unknown patient"
        service = self.requested_service or "an unspecified service"
        payer = self.insurance_company or "the insurer"
        if self.decision is Decision.PENDING:
            return f"{name} — request is pending review for {service}."
        if self.decision is Decision.UNKNOWN:
            return f"{name} — request status is unknown for {service}."
        decision = self.decision.value.upper()
        line = f"{name} — {payer} {decision} the request for {service}."
        if self.decision is Decision.DENIED and self.denial_reason:
            line += f" Reason: {self.denial_reason}"
        return line
