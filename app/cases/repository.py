"""SQLite-backed repository for :class:`CaseRecord`.

Workflow scalars are stored as columns (for querying/filtering); the composed
pydantic artifacts are stored as JSON text. Reconstruction validates the JSON
back into the original pydantic models, so a round-trip is lossless.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from app.models.appeal_letter import AppealLetter
from app.models.case_record import (
    CaseRecord,
    CaseStatus,
    HumanReviewDecision,
)
from app.models.patient_case import PatientCase
from app.models.review_result import ReviewResult
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


def new_case_id() -> str:
    """Generate a unique case id."""
    return f"CASE-{uuid.uuid4().hex[:12].upper()}"


def _dump(model) -> Optional[str]:
    """Serialize a pydantic model to JSON text, or None."""
    if model is None:
        return None
    return model.model_dump_json()


def _load(cls, text: Optional[str]):
    """Reconstruct a pydantic model from JSON text, or None."""
    if not text:
        return None
    return cls.model_validate_json(text)


class CaseRepository:
    """CRUD + query operations for cases."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    # ------------------------------------------------------------------ #
    # Serialization helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> CaseRecord:
        decisions_raw = json.loads(row["review_decisions_json"] or "[]")
        decisions = [HumanReviewDecision.model_validate(d) for d in decisions_raw]
        return CaseRecord(
            case_id=row["case_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            status=CaseStatus(row["status"]),
            source_filename=row["source_filename"],
            assigned_reviewer=row["assigned_reviewer"],
            review_notes=row["review_notes"] or "",
            processing_seconds=row["processing_seconds"],
            patient_case=_load(PatientCase, row["patient_case_json"]),
            review_result=_load(ReviewResult, row["review_result_json"]),
            appeal_letter=_load(AppealLetter, row["appeal_letter_json"]),
            review_decisions=decisions,
        )

    # ------------------------------------------------------------------ #
    # Create / update
    # ------------------------------------------------------------------ #
    def create(self, record: CaseRecord) -> CaseRecord:
        """Insert a new case record."""
        self.conn.execute(
            """
            INSERT INTO cases (
                case_id, created_at, updated_at, status, source_filename,
                assigned_reviewer, review_notes, processing_seconds,
                patient_case_json, review_result_json, appeal_letter_json,
                review_decisions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.case_id,
                record.created_at,
                record.updated_at,
                record.status.value,
                record.source_filename,
                record.assigned_reviewer,
                record.review_notes,
                record.processing_seconds,
                _dump(record.patient_case),
                _dump(record.review_result),
                _dump(record.appeal_letter),
                json.dumps([d.model_dump(mode="json") for d in record.review_decisions]),
            ),
        )
        self.conn.commit()
        return record

    def save(self, record: CaseRecord) -> CaseRecord:
        """Upsert a case record (insert if new, else update)."""
        if self.get(record.case_id) is None:
            return self.create(record)
        record.touch()
        self.conn.execute(
            """
            UPDATE cases SET
                updated_at = ?, status = ?, source_filename = ?,
                assigned_reviewer = ?, review_notes = ?, processing_seconds = ?,
                patient_case_json = ?, review_result_json = ?,
                appeal_letter_json = ?, review_decisions_json = ?
            WHERE case_id = ?
            """,
            (
                record.updated_at,
                record.status.value,
                record.source_filename,
                record.assigned_reviewer,
                record.review_notes,
                record.processing_seconds,
                _dump(record.patient_case),
                _dump(record.review_result),
                _dump(record.appeal_letter),
                json.dumps([d.model_dump(mode="json") for d in record.review_decisions]),
                record.case_id,
            ),
        )
        self.conn.commit()
        return record

    # ------------------------------------------------------------------ #
    # Read / query
    # ------------------------------------------------------------------ #
    def get(self, case_id: str) -> Optional[CaseRecord]:
        row = self.conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def all(self) -> list[CaseRecord]:
        """Return all cases, most recently updated first."""
        rows = self.conn.execute(
            "SELECT * FROM cases ORDER BY updated_at DESC, rowid DESC"
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def by_status(self, status: CaseStatus | str) -> list[CaseRecord]:
        s = status.value if isinstance(status, CaseStatus) else str(status)
        rows = self.conn.execute(
            "SELECT * FROM cases WHERE status = ? ORDER BY updated_at DESC, rowid DESC",
            (s,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM cases").fetchone()
        return int(row["c"])

    def delete(self, case_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
