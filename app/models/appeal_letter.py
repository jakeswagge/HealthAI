"""Pydantic model for a generated prior-authorization appeal letter.

An :class:`AppealLetter` is the validated output of the appeal engine
(Claude-backed or deterministic). It contains both the structured fields used
to assemble/justify the appeal and the final rendered ``letter_text``.

Safety stance
-------------
The appeal must never assert clinical facts that are not supported by the
inputs (a treatment that occurred, a diagnosis, or a test result). When such
information is absent, the appropriate language is "Documentation was not
available" or "Additional clinical evidence may be required". The model itself
stays neutral about content; the prompt + deterministic builder enforce the
wording, and tests assert it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator

from app.models.safety import AppealVerificationResult


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class AppealLetter(BaseModel):
    """Structured representation of a prior-authorization appeal letter."""

    appeal_id: str = Field(..., description="Unique identifier for this appeal.")
    created_at: str = Field(
        default_factory=_utc_now_iso,
        description="ISO-8601 timestamp of when the appeal was generated.",
    )

    patient_name: Optional[str] = Field(
        default=None, description="Patient/member name."
    )
    member_id: Optional[str] = Field(default=None, description="Member ID.")
    insurance_company: Optional[str] = Field(
        default=None, description="Payer / insurance company."
    )
    requested_service: Optional[str] = Field(
        default=None, description="Service or drug being appealed."
    )
    original_decision: Optional[str] = Field(
        default=None, description="The original determination (e.g. denied)."
    )

    # --- Final Milestone: payer / guideline-pack provenance (optional) --- #
    payer_id: Optional[str] = Field(
        default=None, description="Payer whose policy governed this appeal."
    )
    guideline_pack: Optional[str] = Field(
        default=None, description="Guideline pack id used for this appeal."
    )
    guideline_version: Optional[str] = Field(
        default=None, description="Version of the guideline/pack used."
    )

    appeal_reason: str = Field(
        default="",
        description="The central argument for why the denial should be overturned.",
    )
    clinical_summary: str = Field(
        default="",
        description="Factually grounded summary of the clinical background.",
    )
    guideline_support: list[str] = Field(
        default_factory=list,
        description="Guideline criteria / citations supporting approval.",
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="Evidence that was not available and may be required.",
    )
    recommended_next_steps: list[str] = Field(
        default_factory=list,
        description="Concrete next steps for the provider/reviewer.",
    )
    letter_text: str = Field(
        default="",
        description="The full, formatted appeal letter ready to send.",
    )
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the appeal's strength/quality (0.0-1.0).",
    )

    # Optional traceability (Milestone 6/7). Maps an appeal section name to the
    # EvidenceReference ids that support it. Backward-compatible (empty by
    # default). The deterministic builder populates this; statements without
    # support are flagged rather than fabricated.
    section_evidence: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per-section supporting evidence ids.",
    )

    # --- Milestone 6/7: evidence traceability (back-compatible additions) --- #
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="All EvidenceReference ids cited anywhere in this appeal.",
    )
    section_evidence: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Map of appeal section name -> supporting evidence ids.",
    )
    citations: list[str] = Field(
        default_factory=list,
        description="Human-readable source citations (e.g. '(clinical_note.pdf, p.4)').",
    )
    verification: AppealVerificationResult = Field(
        default_factory=AppealVerificationResult,
        description="Quote/evidence verification result for generated appeal claims.",
    )
    drafted_by_ai: bool = Field(
        default=False,
        description="Whether a real AI backend drafted the appeal narrative.",
    )
    draft_backend: Optional[str] = Field(
        default=None, description="Backend that drafted the appeal narrative."
    )
    draft_model: Optional[str] = Field(
        default=None, description="Model/backend identifier used for appeal drafting."
    )
    safety_gate: dict = Field(
        default_factory=dict,
        description="Latest safety-gate outcome for this appeal.",
    )

    # ------------------------------------------------------------------ #
    # Coercion / validation
    # ------------------------------------------------------------------ #
    @field_validator(
        "patient_name",
        "member_id",
        "insurance_company",
        "requested_service",
        "original_decision",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if s == "" or s.lower() in {"null", "none", "n/a", "na"}:
                return None
            return s
        return v

    @field_validator(
        "appeal_reason", "clinical_summary", "letter_text", mode="before"
    )
    @classmethod
    def _coerce_text(cls, v):
        if v is None:
            return ""
        return str(v).strip()

    @field_validator(
        "guideline_support",
        "missing_information",
        "recommended_next_steps",
        "evidence_ids",
        "citations",
        mode="before",
    )
    @classmethod
    def _coerce_str_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        cleaned: list[str] = []
        for item in v:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                cleaned.append(s)
        return cleaned

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
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
    def has_letter(self) -> bool:
        """True if a non-trivial letter body was produced."""
        return len(self.letter_text.strip()) > 0

    def summary(self) -> str:
        """One-line human-readable summary of the appeal."""
        who = self.patient_name or "the member"
        svc = self.requested_service or "the requested service"
        payer = self.insurance_company or "the payer"
        return f"Appeal to {payer} on behalf of {who} regarding {svc}."

    def to_markdown(self) -> str:
        """Return the letter as Markdown (letter body is already formatted)."""
        return self.letter_text

    def to_txt(self) -> str:
        """Return the letter as plain text."""
        return self.letter_text
