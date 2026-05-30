"""Case management.

Tracks prior-authorization cases through their lifecycle, persisting them in
SQLite. Provides the repository layer and a service that ties together case
persistence, audit logging, and status transitions.

Independent of the extraction, review, and appeal engines: those produce the
artifacts a case composes, but this package does not import their agents.
"""

from app.cases.repository import CaseRepository
from app.cases.document_repository import CaseDocumentRepository
from app.evidence.repository import EvidenceRepository
from app.cases.transitions import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    can_transition,
)
from app.cases.service import CaseService
from app.cases.export import build_export_files, build_export_zip

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
