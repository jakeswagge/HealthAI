"""Shared JSON-parsing + retry utilities for LLM-backed agents.

Milestone 12 (architecture stabilization) extracted these from the four agents
(`MedicalExtractionAgent`, `GuidelineReviewAgent`, `AppealGenerationAgent`,
`ClaudeEvidenceExtractor`) which each had a near-identical copy. Behavior is
preserved exactly; this is a de-duplication, not a logic change.

- :func:`extract_json_object` — robustly pull a single JSON object from model
  output (direct parse, fenced ```json block, or first balanced ``{...}`` span).
- :func:`extract_json_payload` — same strategies, but also accepts a top-level
  JSON array (used by the evidence extractor).
- :class:`RETRY_INSTRUCTION` — the standard corrective re-prompt text.
"""

from __future__ import annotations

import json
import re

# Matches a ```json ... ``` or ``` ... ``` fenced block.
FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _first_balanced_object(text: str) -> dict | None:
    """Return the first balanced ``{...}`` span parsed as a dict, or None."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                snippet = text[start : i + 1]
                try:
                    obj = json.loads(snippet)
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    return None
    return None


def extract_json_object(text: str) -> dict:
    """Best-effort extraction of a single JSON object from model output.

    Handles: clean JSON, fenced JSON blocks, and JSON embedded in prose.

    Raises:
        ValueError: if no parseable JSON object is found.
    """
    if text is None:
        raise ValueError("Model returned no text.")

    candidate = text.strip()

    # 1. Direct parse.
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 2. Fenced code block.
    fence = FENCE_RE.search(candidate)
    if fence:
        try:
            obj = json.loads(fence.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # 3. First balanced { ... } span.
    obj = _first_balanced_object(candidate)
    if obj is not None:
        return obj

    raise ValueError("No valid JSON object found in model output.")


def extract_json_payload(text: str):
    """Extract a JSON object OR array from model output.

    Used where the schema is a top-level array (or an object wrapping one).
    Tries: direct parse, fenced block, then first balanced object span.

    Raises:
        ValueError: if nothing parseable is found.
    """
    if text is None or not str(text).strip():
        raise ValueError("Empty model response.")
    candidate = str(text).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    fence = FENCE_RE.search(candidate)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    obj = _first_balanced_object(candidate)
    if obj is not None:
        return obj

    raise ValueError("No parseable JSON found in model output.")


#: Standard corrective re-prompt appended on a failed parse/validation attempt.
RETRY_INSTRUCTION = (
    "Your previous response was not valid against the required schema. "
    "Error: {error}. Respond again with VALID JSON ONLY containing exactly the "
    "required keys. Use null or [] for unknown values. No commentary."
)


def retry_message(error: object) -> dict[str, str]:
    """Build the standard corrective user message for a retry attempt."""
    return {"role": "user", "content": RETRY_INSTRUCTION.format(error=error)}
