"""SQLite-backed audit-event repository (append-only) + query interface."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models.audit_event import AuditActor, AuditEvent, AuditEventType
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class AuditRepository:
    """Append + query audit events stored in SQLite."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #
    def record(self, event: AuditEvent) -> AuditEvent:
        """Persist an audit event."""
        self.conn.execute(
            """
            INSERT INTO audit_events
                (event_id, timestamp, case_id, event_type, actor, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.timestamp,
                event.case_id,
                event.event_type.value,
                event.actor.value,
                event.details,
            ),
        )
        self.conn.commit()
        return event

    def log(
        self,
        case_id: str,
        event_type: AuditEventType | str,
        details: str = "",
        actor: AuditActor | str = AuditActor.SYSTEM,
    ) -> AuditEvent:
        """Convenience: build + persist an event in one call."""
        event = AuditEvent(
            case_id=case_id,
            event_type=event_type,
            actor=actor,
            details=details,
        )
        return self.record(event)

    # ------------------------------------------------------------------ #
    # Query
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            case_id=row["case_id"],
            event_type=row["event_type"],
            actor=row["actor"],
            details=row["details"] or "",
        )

    def for_case(self, case_id: str) -> list[AuditEvent]:
        """Return all events for a case, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM audit_events WHERE case_id = ? ORDER BY timestamp ASC, rowid ASC",
            (case_id,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def all(self, limit: int | None = None) -> list[AuditEvent]:
        """Return all events, newest first (optionally limited)."""
        sql = "SELECT * FROM audit_events ORDER BY timestamp DESC, rowid DESC"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def by_type(self, event_type: AuditEventType | str) -> list[AuditEvent]:
        """Return all events of a given type, newest first."""
        et = event_type.value if isinstance(event_type, AuditEventType) else str(event_type)
        rows = self.conn.execute(
            "SELECT * FROM audit_events WHERE event_type = ? ORDER BY timestamp DESC, rowid DESC",
            (et,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM audit_events").fetchone()
        return int(row["c"])

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
