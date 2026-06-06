"""Governance-aware explainability (Milestone 13).

Turns a review/appeal plus the case's evidence, reviewer decisions, quality
assessments, and the active :class:`ApprovedEvidenceSet` into auditable
explanations:

- :class:`~app.explainability.engine.ExplainabilityEngine` builds
  :class:`ReviewExplanation`, :class:`AppealExplanation`, and a
  :class:`TraceabilityChain`.

In VALIDATED mode the explanations prove the governance contract: only approved
evidence appears in ``evidence_used``; rejected/excluded evidence appears only
in ``evidence_excluded`` and never influences the recommendation, rationale,
confidence, or appeal body.

Independent of the cases package (no import cycle): the engine receives plain
models from the caller (the CaseService facade wires them together).
"""

from app.explainability.engine import ExplainabilityEngine

__all__ = ["ExplainabilityEngine"]
