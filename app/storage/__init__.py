"""Local persistence helpers (SQLite).

A tiny shared layer that gives the case and audit repositories a consistent way
to open a SQLite database and initialize the schema. No cloud, no ORM - just the
stdlib ``sqlite3`` module, kept local and testable.
"""

from app.storage.database import (
    DEFAULT_DB_PATH,
    connect,
    initialize_schema,
)

__all__ = ["DEFAULT_DB_PATH", "connect", "initialize_schema"]
