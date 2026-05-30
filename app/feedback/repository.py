"""SQLite repository for reviewer feedback (append-only)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models.reviewer_feedback import (
    FeedbackTarget,
    FeedbackVerdict,
    ReviewerFeedback,
)
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class ReviewerFeedbackRepository:
    """Append + query reviewer feedback."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    @staticmethod
    def _row(row: sqlite3.Row) -> ReviewerFeedback:
        return ReviewerFeedback(
            feedback_id=row["feedback_id"],
            case_id=row["case_id"],
            reviewer=row["reviewer"],
            target_type=FeedbackTarget(row["target_type"]),
            target_id=row["target_id"],
            feedback=FeedbackVerdict(row["feedback"]),
            comments=row["comments"] or "",
            timestamp=row["timestamp"],
        )

    def add(self, feedback: ReviewerFeedback) -> ReviewerFeedback:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO reviewer_feedback
                (feedback_id, case_id, reviewer, target_type, target_id,
                 feedback, comments, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback.feedback_id,
                feedback.case_id,
                feedback.reviewer,
                feedback.target_type.value,
                feedback.target_id,
                feedback.feedback.value,
                feedback.comments,
                feedback.timestamp,
            ),
        )
        self.conn.commit()
        return feedback

    def for_case(self, case_id: str) -> list[ReviewerFeedback]:
        rows = self.conn.execute(
            "SELECT * FROM reviewer_feedback WHERE case_id = ? ORDER BY timestamp ASC, rowid ASC",
            (case_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def by_target(self, target_type: FeedbackTarget | str) -> list[ReviewerFeedback]:
        tt = target_type.value if isinstance(target_type, FeedbackTarget) else str(target_type)
        rows = self.conn.execute(
            "SELECT * FROM reviewer_feedback WHERE target_type = ? ORDER BY timestamp DESC, rowid DESC",
            (tt,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def all(self) -> list[ReviewerFeedback]:
        rows = self.conn.execute(
            "SELECT * FROM reviewer_feedback ORDER BY timestamp DESC, rowid DESC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def count_for_case(self, case_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM reviewer_feedback WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        return int(row["c"])

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
