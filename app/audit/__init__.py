"""Audit logging.

Records significant actions against cases as immutable :class:`AuditEvent`
rows in SQLite and provides a query interface. Independent of extraction,
review, and appeals.
"""

from app.audit.repository import AuditRepository

__all__ = ["AuditRepository"]
