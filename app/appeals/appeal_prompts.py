"""Prompt engineering for prior-authorization appeal generation.

The appeal agent asks Claude to produce STRUCTURED JSON FIRST (the appeal
fields) and the GENERATED LETTER SECOND (inside the same JSON, as
``letter_text``). This keeps the output machine-validatable while still
producing a ready-to-send letter.

Safety is paramount: the model must never assert clinical facts that are not
present in the inputs. Missing items must be surfaced with neutral language,
not fabricated.
"""

from __future__ import annotations

import json

from app.models.clinical_guideline import ClinicalGuideline
from app.models.patient_case import PatientCase
from app.models.review_result import ReviewResult

_APPEAL_SCHEMA_EXAMPLE = {
    "appeal_reason": "central argument challenging the denial (string)",
    "clinical_summary": "factually grounded clinical background (string)",
    "guideline_support": ["guideline criteria/citations supporting approval"],
    "missing_information": ["evidence not available that may be required"],
    "recommended_next_steps": ["concrete next steps"],
    "confidence_score": "float between 0.0 and 1.0",
    "letter_text": "the FULL formatted appeal letter as text (string)",
}

APPEAL_SYSTEM_PROMPT = """\
You are a healthcare prior-authorization appeals specialist. You draft \
professional, persuasive, and factually grounded appeal letters that ask a \
payer to overturn a denial.

Absolute safety rules (these override persuasiveness):
1. NEVER claim a treatment occurred, a diagnosis exists, or a test result is \
available unless it is explicitly present in the provided inputs.
2. When information is missing, write "Documentation was not available" or \
"Additional clinical evidence may be required" - do NOT invent it.
3. Cite ONLY the evidence and guideline criteria provided. Do not fabricate \
study names, dates, lab values, or clinical events.
4. Maintain a professional, respectful healthcare tone suitable for an \
insurance appeal.
5. If recommendation is DENY and missing_criteria is not empty, generate a \
Documentation Deficiency Appeal: state that the denial appears related to \
missing or undocumented criteria, list every missing criterion exactly, and do \
not assert medical necessity or criteria satisfaction.

Output rules:
6. Return VALID JSON ONLY - no markdown fences, no commentary outside the JSON.
7. Put the STRUCTURED FIELDS first and the rendered letter in "letter_text".
8. The letter in "letter_text" MUST include these sections, in order, using \
markdown headers (##): Patient Information, Clinical Background, Requested \
Service, Reason For Appeal, Guideline Support, Missing Evidence, Request For \
Reconsideration, Signature.\
"""


def _case_block(case: PatientCase) -> str:
    return json.dumps(
        {
            "patient_name": case.patient_name,
            "member_id": case.member_id,
            "date_of_birth": case.date_of_birth,
            "diagnosis": case.diagnosis,
            "icd10_codes": case.icd10_codes,
            "requested_service": case.requested_service,
            "cpt_codes": case.cpt_codes,
            "insurance_company": case.insurance_company,
            "original_decision": case.decision.value,
            "denial_reason": case.denial_reason,
            "physician_name": case.physician_name,
        },
        indent=2,
    )


def _review_block(review: ReviewResult) -> str:
    return json.dumps(
        {
            "recommendation": review.recommendation.value,
            "matched_criteria": review.matched_criteria,
            "missing_criteria": review.missing_criteria,
            "missing_evidence": review.missing_evidence,
            "recommended_actions": review.recommended_actions,
            "contraindications_found": review.contraindications_found,
            "rationale": review.rationale,
        },
        indent=2,
    )


def _guideline_block(guideline: ClinicalGuideline | None) -> str:
    if guideline is None:
        return "null (no specific guideline matched)"
    return json.dumps(
        {
            "guideline_id": guideline.guideline_id,
            "service_name": guideline.service_name,
            "diagnosis": guideline.diagnosis,
            "version": guideline.version,
            "source": guideline.source,
            "required_criteria": [
                {"id": c.id, "description": c.description}
                for c in guideline.required_criteria
            ],
            "supporting_evidence": guideline.supporting_evidence,
        },
        indent=2,
    )


def build_appeal_user_prompt(
    case: PatientCase,
    review: ReviewResult,
    guideline: ClinicalGuideline | None = None,
) -> str:
    """Build the appeal-generation user prompt."""
    schema = json.dumps(_APPEAL_SCHEMA_EXAMPLE, indent=2)
    return f"""\
Draft a prior-authorization appeal letter using only the information below.

PATIENT CASE:
{_case_block(case)}

CLINICAL REVIEW RESULT:
{_review_block(review)}

APPLICABLE GUIDELINE:
{_guideline_block(guideline)}

Produce a single JSON object with EXACTLY these keys:

{schema}

Reminders:
- Structured fields first; the full letter goes in "letter_text".
- Challenge the denial rationale, reference the guideline support, and clearly
  identify any missing documentation.
- If recommendation is DENY and missing_criteria is not empty, do not state that
  medical necessity is supported, criteria are satisfied, or approval is
  justified. Generate documentation-deficiency wording only.
- Never fabricate clinical facts. Use "Documentation was not available" or
  "Additional clinical evidence may be required" for anything not provided.
- Valid JSON only. No code fences or extra text.
"""


def build_appeal_messages(
    case: PatientCase,
    review: ReviewResult,
    guideline: ClinicalGuideline | None = None,
) -> list[dict[str, str]]:
    """Build the chat messages for the appeal-generation request."""
    return [
        {"role": "user", "content": build_appeal_user_prompt(case, review, guideline)}
    ]
