"""Case management.

Tracks prior-authorization cases through their lifecycle, persisting them in
SQLite. Provides the repository layer and a service that ties together case
persistence, audit logging, and status transitions.

Independent of the extraction, review, and appeal engines: those produce the
artifacts a case composes, but this package does not import their agents.

Import-cycle note (Milestone 12)
--------------------------------
Only the *leaf* modules (repositories + transitions) are imported eagerly here.
The heavier ``CaseService`` / ``export`` symbols are exposed lazily via
:pep:`562` ``__getattr__`` so that importing ``app.cases.repository`` (e.g. from
``app.analytics``) does NOT pull in ``CaseService`` -> ``analytics`` and create a
package-level cycle. The public package API is unchanged.
"""

from app.cases.repository import CaseRepository
from app.cases.document_repository import CaseDocumentRepository
from app.evidence.repository import EvidenceRepository
from app.cases.transitions import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    can_transition,
)

__all__ = [
    "CaseRepository",
    "CaseDocumentRepository",
    "EvidenceRepository",
    "CaseService",
    "ALLOWED_TRANSITIONS",
    "InvalidTransitionError",
    "can_transition",
    "build_export_files",
    "build_export_zip",
]


def __getattr__(name: str):
    """Lazily expose heavy symbols to avoid an import cycle (PEP 562)."""
    if name == "CaseService":
        from app.cases.service import CaseService

        return CaseService
    if name in {"build_export_files", "build_export_zip"}:
        from app.cases import export

        return getattr(export, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
