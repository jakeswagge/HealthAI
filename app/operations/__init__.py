"""Operational diagnostics (Final Milestone).

Local-only operational health monitoring derived from the SQLite audit trail and
governance/quality stores. No external observability platform; everything is
computed on demand.

- :class:`~app.operations.health.OperationalHealthMonitor` builds an
  :class:`OperationalHealthReport` (OCR/extraction/review/appeal failures,
  Claude fallback rate, governance violations, conflict frequency).
"""

from app.operations.health import OperationalHealthMonitor

__all__ = ["OperationalHealthMonitor"]
