"""SQLite repository for evidence quality assessments."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.models.evidence_quality import EvidenceQualityAssessment
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class EvidenceQualityRepository:
    """Persist + query evidence quality assessments."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    @staticmethod
    def _row(row: sqlite3.Row) -> EvidenceQualityAssessment:
        return EvidenceQualityAssessment(
            assessment_id=row["assessment_id"],
            evidence_id=row["evidence_id"],
            case_id=row["case_id"] or "",
            completeness_score=row["completeness_score"],
            relevance_score=row["relevance_score"],
            consistency_score=row["consistency_score"],
            traceability_score=row["traceability_score"],
            overall_score=row["overall_score"],
            issues=json.loads(row["issues_json"] or "[]"),
            timestamp=row["timestamp"],
        )

    def add(self, a: EvidenceQualityAssessment) -> EvidenceQualityAssessment:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO evidence_quality
                (assessment_id, evidence_id, case_id, completeness_score,
                 relevance_score, consistency_score, traceability_score,
                 overall_score, issues_json, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                a.assessment_id, a.evidence_id, a.case_id, a.completeness_score,
                a.relevance_score, a.consistency_score, a.traceability_score,
                a.overall_score, json.dumps(a.issues), a.timestamp,
            ),
        )
        self.conn.commit()
        return a

    def replace_for_case(self, case_id: str, assessments: list[EvidenceQualityAssessment]) -> int:
        self.conn.execute("DELETE FROM evidence_quality WHERE case_id = ?", (case_id,))
        self.conn.commit()
        for a in assessments:
            self.add(a)
        return len(assessments)

    def for_case(self, case_id: str) -> list[EvidenceQualityAssessment]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_quality WHERE case_id = ? ORDER BY overall_score ASC, rowid ASC",
            (case_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def for_evidence(self, evidence_id: str) -> EvidenceQualityAssessment | None:
        row = self.conn.execute(
            "SELECT * FROM evidence_quality WHERE evidence_id = ? ORDER BY rowid DESC LIMIT 1",
            (evidence_id,),
        ).fetchone()
        return self._row(row) if row else None

    def count_for_case(self, case_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM evidence_quality WHERE case_id = ?", (case_id,)
        ).fetchone()
        return int(row["c"])

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
