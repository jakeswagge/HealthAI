"""SQLite repository for reviewer evidence decisions (append-only)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models.evidence_review_decision import (
    EvidenceDecision,
    EvidenceReviewDecision,
)
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class EvidenceReviewDecisionRepository:
    """Append + query reviewer decisions about evidence references."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    @staticmethod
    def _row(row: sqlite3.Row) -> EvidenceReviewDecision:
        return EvidenceReviewDecision(
            decision_id=row["decision_id"],
            evidence_id=row["evidence_id"],
            case_id=row["case_id"] or "",
            reviewer=row["reviewer"],
            decision=EvidenceDecision(row["decision"]),
            comments=row["comments"] or "",
            timestamp=row["timestamp"],
        )

    def add(self, d: EvidenceReviewDecision) -> EvidenceReviewDecision:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO evidence_review_decisions
                (decision_id, evidence_id, case_id, reviewer, decision,
                 comments, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                d.decision_id, d.evidence_id, d.case_id, d.reviewer,
                d.decision.value, d.comments, d.timestamp,
            ),
        )
        self.conn.commit()
        return d

    def for_case(self, case_id: str) -> list[EvidenceReviewDecision]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_review_decisions WHERE case_id = ? ORDER BY timestamp ASC, rowid ASC",
            (case_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def for_evidence(self, evidence_id: str) -> list[EvidenceReviewDecision]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_review_decisions WHERE evidence_id = ? ORDER BY timestamp ASC, rowid ASC",
            (evidence_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def latest_for_evidence(self, evidence_id: str) -> EvidenceReviewDecision | None:
        rows = self.for_evidence(evidence_id)
        return rows[-1] if rows else None

    def count_for_case(self, case_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM evidence_review_decisions WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        return int(row["c"])

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
