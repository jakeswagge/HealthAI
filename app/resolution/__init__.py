"""Human conflict resolution + authoritative facts.

A reviewer resolves a detected conflict by choosing the authoritative value;
the rejected alternatives are preserved and the decision is audited. The
resulting authoritative facts override auto-resolved values for downstream
review and appeal generation.

Independent of extraction, review, appeals, and assembly: it consumes the
conflict report assembly produces and records human decisions.
"""

from app.resolution.repository import (
    AuthoritativeFactRepository,
    ConflictResolutionRepository,
)
from app.resolution.engine import ConflictResolutionEngine

__all__ = [
    "AuthoritativeFactRepository",
    "ConflictResolutionRepository",
    "ConflictResolutionEngine",
]
