"""ExportService: build case export packages.

Extracted from ``CaseService`` during the Milestone 12 facade decomposition.
The actual file/zip assembly lives in :mod:`app.cases.export` (pure functions);
this service is a thin object-oriented wrapper so the facade can offer export
building alongside the ``mark_exported`` audit hook (owned by AppealService).

Behavior is identical to calling the export functions directly - a cohesion
wrapper only. The standalone ``build_export_files`` / ``build_export_zip``
functions remain importable from :mod:`app.cases.export` for existing callers.
"""

from __future__ import annotations

from app.cases import export as _export
from app.models.case_record import CaseRecord


class ExportService:
    """Build export packages (files dict or ZIP bytes) for a case record."""

    def build_export_files(self, record: CaseRecord, events, **kwargs) -> dict:
        """Delegate to :func:`app.cases.export.build_export_files`."""
        return _export.build_export_files(record, events, **kwargs)

    def build_export_zip(self, record: CaseRecord, events, **kwargs) -> bytes:
        """Delegate to :func:`app.cases.export.build_export_zip`."""
        return _export.build_export_zip(record, events, **kwargs)
