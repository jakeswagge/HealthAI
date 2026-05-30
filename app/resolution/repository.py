"""SQLite repositories for conflict resolutions and authoritative facts.

Resolutions are append-only (a full history is preserved). Authoritative facts
are upserted per (case_id, fact_type) so the latest authoritative value is easy
to query, but every change is backed by an audited resolution and the prior
resolutions remain in ``conflict_resolutions``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from app.models.conflict_resolution import (
    AuthoritativeFact,
    ConflictResolution,
    ResolutionSource,
)
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class ConflictResolutionRepository:
    """Append-only store of human conflict resolutions."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    @staticmethod
    def _row(row: sqlite3.Row) -> ConflictResolution:
        return ConflictResolution(
            resolution_id=row["resolution_id"],
            case_id=row["case_id"],
            conflict_id=row["conflict_id"],
            fact_type=row["fact_type"] or "",
            chosen_value=row["chosen_value"],
            rejected_values=json.loads(row["rejected_values_json"] or "[]"),
            reviewer_name=row["reviewer_name"],
            justification=row["justification"] or "",
            timestamp=row["timestamp"],
        )

    def add(self, resolution: ConflictResolution) -> ConflictResolution:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO conflict_resolutions
                (resolution_id, case_id, conflict_id, fact_type, chosen_value,
                 rejected_values_json, reviewer_name, justification, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolution.resolution_id,
                resolution.case_id,
                resolution.conflict_id,
                resolution.fact_type,
                resolution.chosen_value,
                json.dumps(resolution.rejected_values),
                resolution.reviewer_name,
                resolution.justification,
                resolution.timestamp,
            ),
        )
        self.conn.commit()
        return resolution

    def for_case(self, case_id: str) -> list[ConflictResolution]:
        rows = self.conn.execute(
            "SELECT * FROM conflict_resolutions WHERE case_id = ? ORDER BY timestamp ASC, rowid ASC",
            (case_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def for_conflict(self, case_id: str, conflict_id: str) -> list[ConflictResolution]:
        rows = self.conn.execute(
            "SELECT * FROM conflict_resolutions WHERE case_id = ? AND conflict_id = ? "
            "ORDER BY timestamp ASC, rowid ASC",
            (case_id, conflict_id),
        ).fetchall()
        return [self._row(r) for r in rows]

    def count_for_case(self, case_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM conflict_resolutions WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        return int(row["c"])

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()


class AuthoritativeFactRepository:
    """Upsert + query authoritative facts (latest per case+fact_type)."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    @staticmethod
    def _row(row: sqlite3.Row) -> AuthoritativeFact:
        return AuthoritativeFact(
            fact_id=row["fact_id"],
            case_id=row["case_id"],
            fact_type=row["fact_type"],
            value=row["value"],
            source_document=row["source_document"],
            source_page=row["source_page"],
            resolution_source=ResolutionSource(row["resolution_source"]),
            confidence=row["confidence"],
            resolution_id=row["resolution_id"],
            updated_at=row["updated_at"],
        )

    def upsert(self, fact: AuthoritativeFact) -> AuthoritativeFact:
        """Insert or update the authoritative value for (case_id, fact_type)."""
        existing = self.get(fact.case_id, fact.fact_type)
        if existing is not None:
            self.conn.execute(
                """
                UPDATE authoritative_facts SET
                    value = ?, source_document = ?, source_page = ?,
                    resolution_source = ?, confidence = ?, resolution_id = ?,
                    updated_at = ?
                WHERE case_id = ? AND fact_type = ?
                """,
                (
                    fact.value,
                    fact.source_document,
                    fact.source_page,
                    fact.resolution_source.value,
                    fact.confidence,
                    fact.resolution_id,
                    fact.updated_at,
                    fact.case_id,
                    fact.fact_type,
                ),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO authoritative_facts
                    (fact_id, case_id, fact_type, value, source_document,
                     source_page, resolution_source, confidence, resolution_id,
                     updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.fact_id,
                    fact.case_id,
                    fact.fact_type,
                    fact.value,
                    fact.source_document,
                    fact.source_page,
                    fact.resolution_source.value,
                    fact.confidence,
                    fact.resolution_id,
                    fact.updated_at,
                ),
            )
        self.conn.commit()
        return fact

    def get(self, case_id: str, fact_type: str) -> Optional[AuthoritativeFact]:
        row = self.conn.execute(
            "SELECT * FROM authoritative_facts WHERE case_id = ? AND fact_type = ?",
            (case_id, fact_type),
        ).fetchone()
        return self._row(row) if row else None

    def for_case(self, case_id: str) -> list[AuthoritativeFact]:
        rows = self.conn.execute(
            "SELECT * FROM authoritative_facts WHERE case_id = ? ORDER BY fact_type ASC",
            (case_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def human_facts(self, case_id: str) -> list[AuthoritativeFact]:
        return [
            f for f in self.for_case(case_id)
            if f.resolution_source is ResolutionSource.HUMAN
        ]

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
