"""SQLite repository for org-level governance settings (single row)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models.governance import GovernanceSettings
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema

# A single-row settings table keyed by a fixed id.
_SETTINGS_ID = "GLOBAL"


class GovernanceSettingsRepository:
    """Load / save the single global :class:`GovernanceSettings` record."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    def get(self) -> GovernanceSettings:
        """Return the current settings (defaults if none stored yet)."""
        row = self.conn.execute(
            "SELECT * FROM governance_settings WHERE settings_id = ?",
            (_SETTINGS_ID,),
        ).fetchone()
        if row is None:
            return GovernanceSettings()
        return GovernanceSettings(
            validated_evidence_mode=bool(row["validated_evidence_mode"]),
            allow_unreviewed_evidence=bool(row["allow_unreviewed_evidence"]),
            minimum_quality_score=row["minimum_quality_score"],
            require_conflict_resolution=bool(row["require_conflict_resolution"]),
            require_human_review_before_export=bool(
                row["require_human_review_before_export"]
            ),
            confidence_threshold=row["confidence_threshold"],
            block_autonomous_denials=bool(row["block_autonomous_denials"]),
            require_verified_appeal_claims=bool(
                row["require_verified_appeal_claims"]
            ),
        )

    def save(self, settings: GovernanceSettings) -> GovernanceSettings:
        """Upsert the global settings row."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO governance_settings
                (settings_id, validated_evidence_mode, allow_unreviewed_evidence,
                 minimum_quality_score, require_conflict_resolution,
                 require_human_review_before_export, confidence_threshold,
                 block_autonomous_denials, require_verified_appeal_claims, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                _SETTINGS_ID,
                int(settings.validated_evidence_mode),
                int(settings.allow_unreviewed_evidence),
                settings.minimum_quality_score,
                int(settings.require_conflict_resolution),
                int(settings.require_human_review_before_export),
                settings.confidence_threshold,
                int(settings.block_autonomous_denials),
                int(settings.require_verified_appeal_claims),
            ),
        )
        self.conn.commit()
        return settings

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
