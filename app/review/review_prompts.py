"""Prompt engineering for clinical guideline review.

The review agent compares a structured PatientCase against an applicable
ClinicalGuideline and produces a structured ReviewResult. The prompt instructs
Claude to reason about medical-necessity criteria and to return valid JSON
only, never inventing criteria that are not in the supplied guideline.
"""

from __future__ import annotations

from datetime import date
import json

from app.models.clinical_guideline import ClinicalGuideline
from app.models.patient_case import PatientCase

_REVIEW_SCHEMA_EXAMPLE = {
    "guideline_id": "matched supplied guideline id, or null if none applies",
    "service_name": "matched supplied service name, or null if none applies",
    "recommendation": "one of: APPROVE | DENY | INSUFFICIENT_INFORMATION",
    "matched_criteria": ["descriptions of criteria that ARE satisfied"],
    "missing_criteria": ["descriptions of criteria that are NOT satisfied"],
    "missing_evidence": ["specific evidence/documentation still needed"],
    "recommended_actions": ["concrete next steps for the provider/reviewer"],
    "contraindications_found": ["any contraindications detected, else []"],
    "criteria_detail": [
        {
            "id": "criterion id from the supplied guideline",
            "description": "criterion description from the supplied guideline",
            "met": "boolean",
            "status": "one of: met | not_met | unknown",
        "supporting_evidence_ids": ["EvidenceReference ids, if supplied"],
        "not_met_evidence_ids": ["EvidenceReference ids proving the criterion failed, if supplied"],
        "missing_evidence": ["rule-specific missing evidence, if any"],
            "reasoning": "brief rule-level reasoning grounded in the evidence",
            "confidence_score": "float between 0.0 and 1.0",
            "review_backend": "model/backend name",
        }
    ],
    "rationale": "concise explanation of the decision",
    "confidence_score": "float between 0.0 and 1.0",
}


REVIEW_SYSTEM_PROMPT = """\
You are a clinical prior-authorization reviewer. You compare a patient's \
structured case against a specific insurance clinical guideline and decide \
whether the requested service meets medical-necessity criteria.

Strict rules:
1. Use ONLY the criteria provided in the supplied guideline. Do not invent \
criteria or requirements that are not listed.
2. A criterion is "matched" only if the case/evidence clearly supports it. If \
the denial reason states a requirement was not met, treat that criterion as \
NOT satisfied (missing).
3. Evaluate every required criterion independently as met, not_met, or unknown.
   - met: current patient-specific evidence clearly satisfies the criterion.
   - not_met: current patient-specific evidence clearly contradicts or fails the
     criterion.
   - unknown: evidence is missing, stale, historical-only, ambiguous, or not
     patient-specific.
4. recommendation must be exactly one of: APPROVE, DENY, \
INSUFFICIENT_INFORMATION.
   - APPROVE: all required criteria are satisfied and no contraindication.
   - DENY: at least one required criterion is clearly unmet, or a \
contraindication is present.
   - INSUFFICIENT_INFORMATION: evidence is incomplete to decide.
5. Do not treat historical archive material as current evidence for a new prior
authorization request. Old diagnoses or old lab results may support history,
but stale testing or stale provider recommendations are unknown for current
coverage requirements unless the guideline explicitly allows them.
6. For TB screening, a bare mention of TB/tuberculosis is unknown. Negative TB
screening is met only when a patient-specific negative test/result is documented
and is current for the request context. Positive or active TB/infection is a
contraindication.
7. For specialist criteria, generic titles such as MD, DO, physician, NP, or PA
are unknown unless specialty or consultation context is documented. Coordinator
or administrative titles are not specialist-prescriber evidence.
8. Put criteria with status met in matched_criteria. Put criteria with status
not_met or unknown in missing_criteria. Do not leave a criterion out of both
lists.
9. Never fabricate clinical facts. Base everything on the provided inputs.
10. Output VALID JSON ONLY - no markdown, no code fences, no commentary before \
or after the JSON object.\
"""


def _guideline_block(g: ClinicalGuideline) -> str:
    payload = {
        "guideline_id": g.guideline_id,
        "service_name": g.service_name,
        "diagnosis": g.diagnosis,
        "version": g.version,
        "source": g.source,
        "required_criteria": [
            {"id": c.id, "description": c.description, "required": c.required}
            for c in g.required_criteria
        ],
        "contraindications": [
            {"id": c.id, "description": c.description}
            for c in g.contraindications
        ],
        "supporting_evidence": g.supporting_evidence,
    }
    return json.dumps(payload, indent=2)


def _case_block(case: PatientCase) -> str:
    payload = {
        "patient_name": case.patient_name,
        "diagnosis": case.diagnosis,
        "icd10_codes": case.icd10_codes,
        "requested_service": case.requested_service,
        "cpt_codes": case.cpt_codes,
        "insurance_company": case.insurance_company,
        "decision": case.decision.value,
        "denial_reason": case.denial_reason,
        "physician_name": case.physician_name,
        "normalized_clinical_facts": {
            fact: field.normalized_value or field.raw_value
            for fact, field in (case.normalized_fields or {}).items()
        },
        "field_evidence_ids": {
            fact: source.evidence_id
            for fact, source in (case.field_sources or {}).items()
            if source.evidence_id
        },
    }
    return json.dumps(payload, indent=2)


def build_review_user_prompt(
    case: PatientCase,
    guideline: ClinicalGuideline,
    document_text: str | None = None,
) -> str:
    """Build the review user prompt for a case + guideline."""
    schema = json.dumps(_REVIEW_SCHEMA_EXAMPLE, indent=2)
    doc_section = ""
    if document_text:
        # Keep the document bounded, but give the AI enough room to reason about
        # dates, historical sections, and current correspondence.
        snippet = document_text.strip()[:12000]
        doc_section = f"\nSOURCE DOCUMENT (supporting evidence, may be partial):\n\"\"\"\n{snippet}\n\"\"\"\n"

    return f"""\
Review the following prior-authorization case against the clinical guideline.

REVIEW RUN DATE:
{date.today().isoformat()}

CLINICAL GUIDELINE:
{_guideline_block(guideline)}

PATIENT CASE (structured):
{_case_block(case)}
{doc_section}
Produce a single JSON object with EXACTLY these keys:

{schema}

Reminders:
- Use only the guideline's listed criteria.
- Include one criteria_detail object for every required guideline criterion.
- Each criteria_detail.status must be exactly one of: met, not_met, unknown.
- Derive the final recommendation from criteria_detail:
  APPROVE only if every required criterion is met and no contraindication.
  DENY if a criterion is clearly not_met or a contraindication is present.
  INSUFFICIENT_INFORMATION if any required criterion is unknown and none are
  clearly not_met.
- Use only supplied evidence ids in supporting_evidence_ids. If no id is
  supplied for a criterion, return an empty list rather than inventing one.
- If the denial reason indicates an unmet requirement, that criterion is missing.
- Historical archive evidence for an old authorization does not satisfy current
  lab/specialist/current-evaluation requirements for a new request.
- Prefer INSUFFICIENT_INFORMATION when evidence is incomplete.
- Valid JSON only. No code fences or extra text.
"""


def build_review_selection_prompt(
    case: PatientCase,
    guidelines: list[ClinicalGuideline],
    document_text: str | None = None,
) -> str:
    """Build a prompt that lets AI select from supplied guidelines."""
    schema = json.dumps(_REVIEW_SCHEMA_EXAMPLE, indent=2)
    guideline_library = ",\n".join(_guideline_block(g) for g in guidelines)
    doc_section = ""
    if document_text:
        snippet = document_text.strip()[:12000]
        doc_section = f"\nSOURCE DOCUMENT (supporting evidence, may be partial):\n\"\"\"\n{snippet}\n\"\"\"\n"

    return f"""\
Review the following prior-authorization case against the supplied clinical
guideline library.

Choose the single most applicable guideline ONLY from the supplied library.
If none of the supplied guidelines applies to the requested service/diagnosis,
return guideline_id=null, service_name=null, recommendation=INSUFFICIENT_INFORMATION,
and explain that no applicable local guideline was available.

CLINICAL GUIDELINE LIBRARY:
[
{guideline_library}
]

PATIENT CASE (structured):
{_case_block(case)}
{doc_section}
REVIEW RUN DATE:
{date.today().isoformat()}

Produce a single JSON object with EXACTLY these keys:

{schema}

Reminders:
- Use only criteria from the selected supplied guideline.
- Include one criteria_detail object for every required criterion in the
  selected guideline.
- Each criteria_detail.status must be exactly one of: met, not_met, unknown.
- Derive the final recommendation from criteria_detail:
  APPROVE only if every required criterion is met and no contraindication.
  DENY if a criterion is clearly not_met or a contraindication is present.
  INSUFFICIENT_INFORMATION if any required criterion is unknown and none are
  clearly not_met.
- Use only supplied evidence ids in supporting_evidence_ids. If no id is
  supplied for a criterion, return an empty list rather than inventing one.
- Do not invent guideline ids, services, criteria, or clinical facts.
- If the denial reason indicates an unmet requirement, that criterion is missing.
- Historical archive evidence for an old authorization does not satisfy current
  lab/specialist/current-evaluation requirements for a new request.
- Prefer INSUFFICIENT_INFORMATION when evidence is incomplete.
- Valid JSON only. No code fences or extra text.
"""


def build_review_messages(
    case: PatientCase,
    guideline: ClinicalGuideline,
    document_text: str | None = None,
) -> list[dict[str, str]]:
    """Build the chat messages for the review request."""
    return [
        {
            "role": "user",
            "content": build_review_user_prompt(case, guideline, document_text),
        }
    ]


def build_review_selection_messages(
    case: PatientCase,
    guidelines: list[ClinicalGuideline],
    document_text: str | None = None,
) -> list[dict[str, str]]:
    """Build chat messages for AI-guided guideline selection + review."""
    return [
        {
            "role": "user",
            "content": build_review_selection_prompt(case, guidelines, document_text),
        }
    ]
