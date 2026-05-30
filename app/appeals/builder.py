"""Deterministic appeal-letter assembly (offline, no network).

``AppealLetterBuilder`` constructs a complete :class:`AppealLetter` from a
:class:`PatientCase`, a :class:`ReviewResult`, and an optional
:class:`ClinicalGuideline`. It is:

- the local default when no AI backend is configured, and
- the guaranteed fallback when the Claude backend fails or returns invalid
  output after all retries.

Safety
------
The builder NEVER invents clinical facts. It only restates values present on
the inputs. When a value is missing it uses neutral, honest phrasing such as
"Documentation was not available" or "Additional clinical evidence may be
required". The rendered ``letter_text`` always contains the full set of
required sections.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.appeal_letter import AppealLetter
from app.models.clinical_guideline import ClinicalGuideline
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation, ReviewResult

# Canonical safety phrases (asserted by tests).
NOT_AVAILABLE = "Documentation was not available"
MAY_BE_REQUIRED = "Additional clinical evidence may be required"

# Required letter section headers, in order.
SECTION_HEADERS = [
    "Patient Information",
    "Clinical Background",
    "Requested Service",
    "Reason For Appeal",
    "Guideline Support",
    "Missing Evidence",
    "Request For Reconsideration",
    "Signature",
]


def _val(value, fallback: str = NOT_AVAILABLE) -> str:
    """Return a stringified value or a safe fallback when absent."""
    if value is None:
        return fallback
    s = str(value).strip()
    return s if s else fallback


def new_appeal_id() -> str:
    """Generate a unique appeal id."""
    return f"APL-{uuid.uuid4().hex[:12].upper()}"


def _build_clinical_summary(case: PatientCase) -> str:
    """A factually grounded clinical summary, with safe fallbacks."""
    parts: list[str] = []

    if case.diagnosis:
        dx = case.diagnosis
        if case.icd10_codes:
            dx += f" (ICD-10: {', '.join(case.icd10_codes)})"
        parts.append(f"The member carries a documented diagnosis of {dx}.")
    else:
        parts.append(
            f"A specific diagnosis was not documented in the materials reviewed. "
            f"{NOT_AVAILABLE} to confirm the indication."
        )

    if case.physician_name:
        parts.append(f"The request was submitted by {case.physician_name}.")

    parts.append(
        "This summary is limited to information present in the submitted "
        "documentation; no additional clinical events are asserted."
    )
    return " ".join(parts)


def _build_appeal_reason(
    case: PatientCase,
    review: ReviewResult,
) -> str:
    """The central argument challenging the denial."""
    svc = _val(case.requested_service, "the requested service")
    original = case.denial_reason

    lines: list[str] = []
    if original:
        lines.append(
            f"The original determination denied coverage for {svc} on the basis "
            f"that: \"{original}\". We respectfully request reconsideration."
        )
    else:
        lines.append(
            f"We respectfully request reconsideration of the determination "
            f"regarding {svc}."
        )

    if review.matched_criteria:
        lines.append(
            "The following medical-necessity criteria are supported by the "
            "available record: "
            + "; ".join(review.matched_criteria)
            + "."
        )

    if review.missing_criteria:
        lines.append(
            "Where the payer identified unmet criteria, we address them directly "
            "below. Any criterion that cannot be substantiated from the current "
            f"record is noted transparently; {MAY_BE_REQUIRED.lower()} to fully "
            "establish those points."
        )
    else:
        lines.append(
            "Based on the review, the requested service appears to meet the "
            "applicable medical-necessity criteria."
        )
    return " ".join(lines)


def _guideline_support(
    review: ReviewResult,
    guideline: ClinicalGuideline | None,
) -> list[str]:
    support: list[str] = []
    if guideline is not None:
        support.append(
            f"Reviewed against {guideline.guideline_id} - {guideline.service_name} "
            f"({guideline.source}, v{guideline.version})."
        )
        support.extend(guideline.supporting_evidence)
    # Criteria the record supports are the strongest appeal points.
    for c in review.matched_criteria:
        support.append(f"Criterion supported by the record: {c}")
    if not support:
        support.append(
            "No specific guideline citation was available for the requested "
            f"service. {MAY_BE_REQUIRED}."
        )
    return support


def _missing_information(review: ReviewResult) -> list[str]:
    """Combine review missing criteria + evidence into honest gaps."""
    items: list[str] = []
    for c in review.missing_criteria:
        items.append(f"{c} - {NOT_AVAILABLE} in the submitted record.")
    for e in review.missing_evidence:
        if e not in items:
            items.append(e)
    return items


def _next_steps(review: ReviewResult) -> list[str]:
    steps = list(review.recommended_actions)
    if not steps:
        steps = [
            "Submit any additional supporting clinical documentation.",
            f"{MAY_BE_REQUIRED} to complete the medical-necessity review.",
        ]
    return steps


def render_letter_text(
    *,
    appeal_id: str,
    created_at: str,
    case: PatientCase,
    review: ReviewResult,
    guideline: ClinicalGuideline | None,
    appeal_reason: str,
    clinical_summary: str,
    guideline_support: list[str],
    missing_information: list[str],
    recommended_next_steps: list[str],
) -> str:
    """Render the full formatted appeal letter with all required sections."""
    try:
        date_str = datetime.fromisoformat(created_at).strftime("%B %d, %Y")
    except (ValueError, TypeError):
        date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    svc = _val(case.requested_service, "the requested service")
    payer = _val(case.insurance_company, "the payer")

    lines: list[str] = []
    lines.append("# Prior Authorization Appeal Letter")
    lines.append("")
    lines.append(f"Date: {date_str}")
    lines.append(f"Appeal Reference: {appeal_id}")
    lines.append(f"To: {payer}, Appeals Department")
    lines.append("")

    # 1. Patient Information
    lines.append("## Patient Information")
    lines.append(f"- Patient Name: {_val(case.patient_name)}")
    lines.append(f"- Member ID: {_val(case.member_id)}")
    lines.append(f"- Date of Birth: {_val(case.date_of_birth)}")
    lines.append(f"- Insurance Company: {_val(case.insurance_company)}")
    lines.append("")

    # 2. Clinical Background
    lines.append("## Clinical Background")
    lines.append(clinical_summary)
    lines.append("")

    # 3. Requested Service
    lines.append("## Requested Service")
    lines.append(f"- Service/Treatment: {_val(case.requested_service)}")
    lines.append(
        f"- CPT/HCPCS Code(s): "
        f"{', '.join(case.cpt_codes) if case.cpt_codes else NOT_AVAILABLE}"
    )
    lines.append(
        f"- Diagnosis Code(s): "
        f"{', '.join(case.icd10_codes) if case.icd10_codes else NOT_AVAILABLE}"
    )
    lines.append("")

    # 4. Reason For Appeal
    lines.append("## Reason For Appeal")
    lines.append(appeal_reason)
    lines.append("")

    # 5. Guideline Support
    lines.append("## Guideline Support")
    for item in guideline_support:
        lines.append(f"- {item}")
    lines.append("")

    # 6. Missing Evidence (always present; states none if empty)
    lines.append("## Missing Evidence")
    if missing_information:
        lines.append(
            "The following items were not available in the record. We do not "
            "assert these facts; we identify them so they can be supplied:"
        )
        for item in missing_information:
            lines.append(f"- {item}")
    else:
        lines.append(
            "No outstanding evidence gaps were identified in the reviewed "
            "record for the criteria assessed."
        )
    lines.append("")

    # 7. Request For Reconsideration
    lines.append("## Request For Reconsideration")
    lines.append(
        f"On behalf of the member, we respectfully request that {payer} "
        f"reconsider the determination for {svc}. We believe the information "
        "above supports medical necessity. Where documentation was not "
        f"available, {MAY_BE_REQUIRED.lower()}, and we are prepared to supply "
        "it promptly upon request."
    )
    if recommended_next_steps:
        lines.append("")
        lines.append("Recommended next steps:")
        for step in recommended_next_steps:
            lines.append(f"- {step}")
    lines.append("")

    # 8. Signature Placeholder
    lines.append("## Signature")
    lines.append("Respectfully submitted,")
    lines.append("")
    lines.append("__________________________")
    lines.append("[Provider Name]")
    lines.append("[Title / Credentials]")
    lines.append("[Practice / Facility]")
    lines.append("[Contact Information]")
    lines.append("")
    lines.append(
        "_This letter was generated to assist with the appeal process and "
        "should be reviewed and signed by the treating provider before "
        "submission._"
    )

    return "\n".join(lines)


class AppealLetterBuilder:
    """Deterministically assemble an :class:`AppealLetter`."""

    def build(
        self,
        case: PatientCase,
        review: ReviewResult,
        guideline: ClinicalGuideline | None = None,
    ) -> AppealLetter:
        """Build a complete appeal letter from the structured inputs."""
        appeal_id = new_appeal_id()
        created_at = datetime.now(timezone.utc).isoformat()

        clinical_summary = _build_clinical_summary(case)
        appeal_reason = _build_appeal_reason(case, review)
        guideline_support = _guideline_support(review, guideline)
        missing_information = _missing_information(review)
        recommended_next_steps = _next_steps(review)

        letter_text = render_letter_text(
            appeal_id=appeal_id,
            created_at=created_at,
            case=case,
            review=review,
            guideline=guideline,
            appeal_reason=appeal_reason,
            clinical_summary=clinical_summary,
            guideline_support=guideline_support,
            missing_information=missing_information,
            recommended_next_steps=recommended_next_steps,
        )

        # Confidence: anchored to the review's confidence, tempered by how much
        # of the record supports the request.
        total = max(1, len(review.matched_criteria) + len(review.missing_criteria))
        support_ratio = len(review.matched_criteria) / total
        confidence = round(
            min(0.95, 0.4 + 0.4 * support_ratio + 0.15 * review.confidence_score),
            3,
        )

        original_decision = (
            case.decision.value if case.decision is not Decision.UNKNOWN else None
        )

        return AppealLetter(
            appeal_id=appeal_id,
            created_at=created_at,
            patient_name=case.patient_name,
            member_id=case.member_id,
            insurance_company=case.insurance_company,
            requested_service=case.requested_service,
            original_decision=original_decision,
            appeal_reason=appeal_reason,
            clinical_summary=clinical_summary,
            guideline_support=guideline_support,
            missing_information=missing_information,
            recommended_next_steps=recommended_next_steps,
            letter_text=letter_text,
            confidence_score=confidence,
        )
