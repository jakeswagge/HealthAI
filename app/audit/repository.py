"""SQLite-backed audit-event repository (append-only) + query interface."""

from __future__ import annotations

import hashlib
import json
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
        previous_hash = event.previous_hash or self._latest_hash()
        payload_hash = event.payload_hash or hashlib.sha256(
            (event.details or "").encode("utf-8")
        ).hexdigest()
        event_hash = event.event_hash or self._event_hash(
            event,
            previous_hash=previous_hash,
            payload_hash=payload_hash,
        )
        event = event.model_copy(
            update={
                "previous_hash": previous_hash,
                "payload_hash": payload_hash,
                "event_hash": event_hash,
                "resource_type": event.resource_type or "case",
                "resource_id": event.resource_id or event.case_id,
                "action": event.action or event.event_type.value,
            }
        )
        self.conn.execute(
            """
            INSERT INTO audit_events
                (event_id, timestamp, case_id, event_type, actor, details,
                 previous_hash, event_hash, resource_type, resource_id, action,
                 payload_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.timestamp,
                event.case_id,
                event.event_type.value,
                event.actor.value,
                event.details,
                event.previous_hash,
                event.event_hash,
                event.resource_type,
                event.resource_id,
                event.action,
                event.payload_hash,
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
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            action=row["action"],
            payload_hash=row["payload_hash"],
        )

    def _latest_hash(self) -> str | None:
        row = self.conn.execute(
            """
            SELECT event_hash FROM audit_events
            WHERE event_hash IS NOT NULL
            ORDER BY timestamp DESC, rowid DESC
            LIMIT 1
            """
        ).fetchone()
        return row["event_hash"] if row else None

    @staticmethod
    def _event_hash(
        event: AuditEvent,
        *,
        previous_hash: str | None,
        payload_hash: str,
    ) -> str:
        payload = {
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "case_id": event.case_id,
            "event_type": event.event_type.value,
            "actor": event.actor.value,
            "details": event.details,
            "previous_hash": previous_hash,
            "payload_hash": payload_hash,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

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
