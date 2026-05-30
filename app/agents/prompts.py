"""Prompt engineering for medical document extraction.

These prompts are tuned for U.S. prior-authorization correspondence:
- Prior authorization letters
- Insurance denials
- Insurance approvals
- Medical necessity reviews

Design goals:
- Extract ONLY the supported schema fields.
- Never hallucinate; emit ``null`` (or ``[]`` for code lists) when unknown.
- Return valid JSON only, with no prose, so it parses deterministically.
"""

from __future__ import annotations

import json

# The exact JSON shape we expect back. Kept in one place so the prompt and the
# schema documentation never drift apart.
_JSON_SCHEMA_EXAMPLE = {
    "patient_name": "string or null",
    "member_id": "string or null",
    "date_of_birth": "string or null (as written in the document)",
    "diagnosis": "string or null (primary diagnosis description)",
    "icd10_codes": ["array of ICD-10 code strings, [] if none"],
    "requested_service": "string or null (procedure/service)",
    "cpt_codes": ["array of CPT/HCPCS code strings, [] if none"],
    "insurance_company": "string or null (payer name)",
    "decision": "one of: approved | denied | partial | unknown",
    "denial_reason": "string or null (null unless decision is denied)",
    "physician_name": "string or null (requesting/ordering provider)",
    "confidence_score": "float between 0.0 and 1.0",
}


EXTRACTION_SYSTEM_PROMPT = """\
You are a meticulous medical document extraction engine for healthcare prior \
authorization workflows. You read insurance correspondence (prior \
authorization letters, denials, approvals, and medical necessity reviews) and \
convert it into structured JSON.

Strict rules:
1. Extract ONLY the fields defined in the provided schema. Do not invent new \
fields.
2. NEVER hallucinate or guess. If a value is not clearly present in the text, \
return null (or an empty array [] for code lists).
3. Copy values as written in the document. Do not paraphrase names, IDs, or \
codes. Normalize codes by stripping surrounding text (e.g. "CPT 73721" -> \
"73721"; "ICD-10: M23.205" -> "M23.205").
4. "decision" must be exactly one of: approved, denied, partial, unknown. \
Use "denied" for adverse determinations, "approved" for favorable/authorized \
determinations, "partial" when only part of the request is approved, and \
"unknown" if the determination is unclear.
5. "denial_reason" must be null unless the decision is denied.
6. "confidence_score" is YOUR self-assessed confidence (0.0-1.0) that the \
extraction is correct and complete given the source text.
7. Output VALID JSON ONLY. No markdown, no code fences, no commentary, no \
explanation before or after the JSON object.\
"""


def _schema_block() -> str:
    """Render the schema example as pretty JSON for the prompt."""
    return json.dumps(_JSON_SCHEMA_EXAMPLE, indent=2)


def build_user_prompt(document_text: str) -> str:
    """Build the user-turn prompt for a single document.

    Args:
        document_text: Raw extracted text of the insurance document.

    Returns:
        The user message instructing Claude to extract structured fields.
    """
    return f"""\
Extract the structured prior-authorization fields from the document below.

Return a single JSON object with EXACTLY these keys (use null / [] when a \
value is not present in the text):

{_schema_block()}

Reminders:
- Valid JSON only. No code fences or extra text.
- Never fabricate values. Prefer null over a guess.
- denial_reason is null unless decision == "denied".

--- BEGIN DOCUMENT ---
{document_text}
--- END DOCUMENT ---
"""


def build_extraction_messages(document_text: str) -> list[dict[str, str]]:
    """Build the chat messages list (user turn) for the extraction request.

    The system prompt is passed separately by the service layer.
    """
    return [{"role": "user", "content": build_user_prompt(document_text)}]
