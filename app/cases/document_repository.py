"""SQLite repository for :class:`CaseDocument` (multi-document support)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from app.models.case_document import CaseDocument, DocumentCategory
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class CaseDocumentRepository:
    """CRUD + queries for documents attached to cases."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    @staticmethod
    def _row(row: sqlite3.Row) -> CaseDocument:
        return CaseDocument(
            document_id=row["document_id"],
            case_id=row["case_id"],
            filename=row["filename"],
            document_type=DocumentCategory(row["document_type"]),
            uploaded_at=row["uploaded_at"],
            page_count=row["page_count"],
            raw_text=row["raw_text"] or "",
        )

    def add(self, document: CaseDocument) -> CaseDocument:
        """Insert (or replace) a document."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO case_documents
                (document_id, case_id, filename, document_type, uploaded_at,
                 page_count, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.document_id,
                document.case_id,
                document.filename,
                document.document_type.value,
                document.uploaded_at,
                document.page_count,
                document.raw_text,
            ),
        )
        self.conn.commit()
        return document

    def get(self, document_id: str) -> Optional[CaseDocument]:
        row = self.conn.execute(
            "SELECT * FROM case_documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        return self._row(row) if row else None

    def for_case(self, case_id: str) -> list[CaseDocument]:
        rows = self.conn.execute(
            "SELECT * FROM case_documents WHERE case_id = ? ORDER BY uploaded_at ASC, rowid ASC",
            (case_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def count_for_case(self, case_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM case_documents WHERE case_id = ?", (case_id,)
        ).fetchone()
        return int(row["c"])

    def delete(self, document_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM case_documents WHERE document_id = ?", (document_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
