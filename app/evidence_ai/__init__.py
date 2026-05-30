"""Claude-backed evidence extraction.

The :class:`ClaudeEvidenceExtractor` produces :class:`EvidenceReference` objects
using Claude (via the LLM service layer) when a real AI backend is configured,
and falls back to the deterministic regex extractor otherwise. It enforces the
healthcare-safety contract: every reference must include verbatim quoted text
that ACTUALLY appears in the source document, or it is rejected (never
fabricated). The output is ordinary EvidenceReference objects, so everything
downstream (quality scoring, assembly, conflicts, review, appeal) is unchanged.
"""

from app.evidence_ai.extractor import (
    ClaudeEvidenceExtractor,
    EVIDENCE_EXTRACTION_SYSTEM_PROMPT,
)

__all__ = ["ClaudeEvidenceExtractor", "EVIDENCE_EXTRACTION_SYSTEM_PROMPT"]
