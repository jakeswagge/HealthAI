"""AssemblyService: orchestrate multi-document case assembly + evidence.

Ties together:
- :class:`CaseDocumentRepository` (persist uploaded documents),
- :class:`CaseAssemblyEngine` (combine docs into a UnifiedCaseContext),
- :class:`EvidenceRepository` (persist the evidence inventory), and
- the audit log (record document-add, assembly, and conflict events).

It does NOT run the review/appeal agents - it produces the assembled context
(including a merged ``PatientCase``) that those engines consume. This keeps the
assembly/evidence concern separate from extraction, review, appeals, and audit
while still recording the workflow.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from app.assembly.engine import CaseAssemblyEngine
from app.audit.repository import AuditRepository
from app.cases.document_repository import CaseDocumentRepository
from app.cases.evidence_repository import EvidenceRepository
from app.extraction.extractor import extract_pages_from_bytes, join_pages
from app.models.audit_event import AuditActor, AuditEventType
from app.models.case_document import CaseDocument, classify_document
from app.models.unified_case_context import UnifiedCaseContext
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class AssemblyService:
    """High-level operations for multi-document cases + evidence."""

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)
        self.documents = CaseDocumentRepository(conn=self.conn)
        self.evidence = EvidenceRepository(conn=self.conn)
        self.audit = AuditRepository(conn=self.conn)
        self.engine = CaseAssemblyEngine()

    # ------------------------------------------------------------------ #
    # Documents
    # ------------------------------------------------------------------ #
    def add_document_from_bytes(
        self,
        case_id: str,
        filename: str,
        data: bytes,
        document_type: Optional[str] = None,
    ) -> CaseDocument:
        """Extract page-aware text, classify, persist, and audit a document."""
        pages = extract_pages_from_bytes(filename, data)
        raw_text = join_pages(pages)
        classification = (
            document_type
            if document_type is not None
            else classify_document("\n".join(pages), filename)
        )
        document = CaseDocument(
            case_id=case_id,
            filename=filename,
            document_type=classification,
            page_count=len(pages),
            raw_text=raw_text,
        )
        self.documents.add(document)
        self.audit.log(
            case_id,
            AuditEventType.CASE_DOCUMENT_ADDED,
            details=f"Added {document.document_type.value} '{filename}' ({document.page_count} page(s)).",
            actor=AuditActor.USER,
        )
        return document

    def list_documents(self, case_id: str) -> list[CaseDocument]:
        return self.documents.for_case(case_id)

    # ------------------------------------------------------------------ #
    # Assembly
    # ------------------------------------------------------------------ #
    def assemble(self, case_id: str) -> UnifiedCaseContext:
        """Assemble all of a case's documents into a UnifiedCaseContext.

        Persists the evidence inventory (replacing any prior evidence for the
        case so re-assembly is idempotent) and records audit events for the
        assembly and any detected conflicts.
        """
        documents = self.documents.for_case(case_id)
        context = self.engine.assemble(case_id, documents)

        # Persist evidence (idempotent: clear then re-add).
        self.evidence.delete_for_case(case_id)
        self.evidence.add_many(context.evidence)

        self.audit.log(
            case_id,
            AuditEventType.CASE_ASSEMBLED,
            details=(
                f"Assembled {context.document_count} document(s); "
                f"{len(context.evidence)} evidence reference(s); "
                f"{len(context.missing_information)} missing field(s)."
            ),
        )

        if context.conflict_report.has_conflicts:
            severity = context.conflict_report.highest_severity
            self.audit.log(
                case_id,
                AuditEventType.CONFLICT_DETECTED,
                details=(
                    f"{len(context.conflict_report.conflicts)} conflict(s) "
                    f"detected (highest severity: {severity.value if severity else 'n/a'})."
                ),
            )

        return context

    def get_evidence(self, case_id: str):
        return self.evidence.for_case(case_id)

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
