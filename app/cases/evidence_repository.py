"""SQLite-backed repository for :class:`EvidenceReference` rows.

Evidence references must survive export and be queryable by case and by source
document, so the workflow can always answer "what document/page did this come
from?".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models.evidence import EvidenceReference
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class EvidenceRepository:
    """CRUD + query for evidence references."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    @staticmethod
    def _row_to_ev(row: sqlite3.Row) -> EvidenceReference:
        return EvidenceReference(
            evidence_id=row["evidence_id"],
            case_id=row["case_id"],
            source_document_id=row["source_document_id"],
            page_number=row["page_number"],
            section_label=row["section_label"],
            quoted_text=row["quoted_text"] or "",
            normalized_fact=row["normalized_fact"] or "",
            confidence_score=row["confidence_score"],
            created_at=row["created_at"],
            source_filename=row["source_filename"],
            field_name=row["field_name"],
        )

    def add(self, evidence: EvidenceReference) -> EvidenceReference:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO evidence_references
                (evidence_id, case_id, source_document_id, page_number,
                 section_label, quoted_text, normalized_fact, confidence_score,
                 created_at, source_filename, field_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence.evidence_id,
                evidence.case_id,
                evidence.source_document_id,
                evidence.page_number,
                evidence.section_label,
                evidence.quoted_text,
                evidence.normalized_fact,
                evidence.confidence_score,
                evidence.created_at,
                evidence.source_filename,
                evidence.field_name,
            ),
        )
        self.conn.commit()
        return evidence

    def add_many(self, evidences: list[EvidenceReference]) -> int:
        for ev in evidences:
            self.add(ev)
        return len(evidences)

    def get(self, evidence_id: str) -> EvidenceReference | None:
        row = self.conn.execute(
            "SELECT * FROM evidence_references WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        return self._row_to_ev(row) if row else None

    def for_case(self, case_id: str) -> list[EvidenceReference]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_references WHERE case_id = ? ORDER BY created_at ASC, rowid ASC",
            (case_id,),
        ).fetchall()
        return [self._row_to_ev(r) for r in rows]

    def for_document(self, document_id: str) -> list[EvidenceReference]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_references WHERE source_document_id = ? ORDER BY rowid ASC",
            (document_id,),
        ).fetchall()
        return [self._row_to_ev(r) for r in rows]

    def delete_for_case(self, case_id: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM evidence_references WHERE case_id = ?", (case_id,)
        )
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
