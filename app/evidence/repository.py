"""SQLite repository for :class:`EvidenceReference` (traceability store)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from app.models.evidence_reference import EvidenceReference
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class EvidenceRepository:
    """CRUD + queries for source-backed evidence references."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    @staticmethod
    def _row(row: sqlite3.Row) -> EvidenceReference:
        return EvidenceReference(
            evidence_id=row["evidence_id"],
            case_id=row["case_id"],
            source_document_id=row["source_document_id"],
            source_filename=row["source_filename"],
            page_number=row["page_number"],
            section_label=row["section_label"],
            quoted_text=row["quoted_text"] or "",
            normalized_fact=row["normalized_fact"] or "",
            fact_type=row["fact_type"],
            confidence_score=row["confidence_score"],
            created_at=row["created_at"],
        )

    def add(self, ref: EvidenceReference) -> EvidenceReference:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO evidence_references
                (evidence_id, case_id, source_document_id, source_filename,
                 page_number, section_label, quoted_text, normalized_fact,
                 fact_type, confidence_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ref.evidence_id,
                ref.case_id,
                ref.source_document_id,
                ref.source_filename,
                ref.page_number,
                ref.section_label,
                ref.quoted_text,
                ref.normalized_fact,
                ref.fact_type,
                ref.confidence_score,
                ref.created_at,
            ),
        )
        self.conn.commit()
        return ref

    def add_many(self, refs: list[EvidenceReference]) -> int:
        for ref in refs:
            self.add(ref)
        return len(refs)

    def replace_for_case(self, case_id: str, refs: list[EvidenceReference]) -> int:
        """Replace all evidence for a case (used when re-assembling)."""
        self.conn.execute(
            "DELETE FROM evidence_references WHERE case_id = ?", (case_id,)
        )
        self.conn.commit()
        return self.add_many(refs)

    def get(self, evidence_id: str) -> Optional[EvidenceReference]:
        row = self.conn.execute(
            "SELECT * FROM evidence_references WHERE evidence_id = ?", (evidence_id,)
        ).fetchone()
        return self._row(row) if row else None

    def for_case(self, case_id: str) -> list[EvidenceReference]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_references WHERE case_id = ? ORDER BY rowid ASC",
            (case_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def for_document(self, document_id: str) -> list[EvidenceReference]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_references WHERE source_document_id = ? ORDER BY page_number ASC, rowid ASC",
            (document_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def count_for_case(self, case_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM evidence_references WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        return int(row["c"])

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
