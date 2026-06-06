"""OperationalHealthMonitor: build an OperationalHealthReport locally.

Derives operational signals from the local audit trail plus (optionally) a
governance compliance callable and the assembly engine for conflict frequency.
No external observability; everything is read-only and on demand.

Audit detail markers
--------------------
Failures/degradations are surfaced in audit ``details`` text by the workflow.
The monitor scans (case-insensitive) for stable substrings:

- OCR failure:        "ocr unavailable"
- low-confidence OCR: "low-confidence ocr"  (warning, not a hard failure)
- extraction failure: "extraction failed"
- review failure:     "review failed"
- appeal failure:     "appeal failed"
- Claude fallback:    "fell back" / "degraded to"

This keeps detection decoupled from agent internals: any component that records
one of these phrases is counted, with zero behavior change to the agents.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Optional

from app.assembly.engine import CaseAssemblyEngine
from app.audit.repository import AuditRepository
from app.models.audit_event import AuditEventType
from app.models.operational_health import OperationalHealthReport
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema

# Detail-text markers (lower-cased).
_OCR_FAIL = "ocr unavailable"
_LOW_CONF = "low-confidence ocr"
_EXTRACTION_FAIL = "extraction failed"
_REVIEW_FAIL = "review failed"
_APPEAL_FAIL = "appeal failed"
_FALLBACK_MARKERS = ("fell back", "degraded to", "claude fallback")


class OperationalHealthMonitor:
    """Compute an :class:`OperationalHealthReport` from local storage."""

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        case_repository=None,
        document_repository=None,
        assembly: CaseAssemblyEngine | None = None,
        compliance_fn: Optional[Callable[[str], object]] = None,
    ) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)
        if case_repository is None or document_repository is None:
            from app.cases.document_repository import CaseDocumentRepository
            from app.cases.repository import CaseRepository

            case_repository = case_repository or CaseRepository(conn=self.conn)
            document_repository = document_repository or CaseDocumentRepository(
                conn=self.conn
            )
        self.cases = case_repository
        self.documents = document_repository
        self.audit = AuditRepository(conn=self.conn)
        self.assembly = assembly or CaseAssemblyEngine()
        # Optional: a callable returning a GovernanceComplianceReport for a case.
        self.compliance_fn = compliance_fn

    # ------------------------------------------------------------------ #
    # Collection
    # ------------------------------------------------------------------ #
    def collect(self) -> OperationalHealthReport:
        events = self.audit.all(limit=None)
        cases = self.cases.all()
        total_cases = len(cases)

        documents_processed = sum(
            1 for e in events if e.event_type is AuditEventType.CASE_DOCUMENT_ADDED
        ) + sum(
            1 for e in events if e.event_type is AuditEventType.DOCUMENT_UPLOADED
        )

        ocr_failures = 0
        low_conf = 0
        extraction_failures = 0
        review_failures = 0
        appeal_failures = 0
        claude_fallbacks = 0

        for e in events:
            d = (e.details or "").lower()
            if not d:
                continue
            if _OCR_FAIL in d:
                ocr_failures += 1
            if _LOW_CONF in d:
                low_conf += 1
            if _EXTRACTION_FAIL in d:
                extraction_failures += 1
            if _REVIEW_FAIL in d:
                review_failures += 1
            if _APPEAL_FAIL in d:
                appeal_failures += 1
            if any(m in d for m in _FALLBACK_MARKERS):
                claude_fallbacks += 1

        # AI operations attempted = reviews + appeals generated (proxy).
        ai_ops = sum(
            1 for e in events
            if e.event_type
            in (
                AuditEventType.REVIEW_COMPLETED,
                AuditEventType.APPEAL_GENERATED,
                AuditEventType.REVIEW_EXPLANATION_GENERATED,
                AuditEventType.APPEAL_EXPLANATION_GENERATED,
            )
        )
        claude_fallback_rate = (
            round(claude_fallbacks / ai_ops, 4) if ai_ops else 0.0
        )

        # Conflict frequency (cases with >=1 detected conflict), like analytics.
        cases_with_conflict = 0
        for c in cases:
            docs = self.documents.for_case(c.case_id)
            if not docs:
                continue
            report = self.assembly.assemble(c.case_id, docs).conflict_report
            if report.has_conflicts:
                cases_with_conflict += 1
        conflict_frequency = (
            round(cases_with_conflict / total_cases, 4) if total_cases else 0.0
        )

        # Governance violations (optional, only if a compliance callable given).
        governance_violations = 0
        if self.compliance_fn is not None:
            for c in cases:
                try:
                    rep = self.compliance_fn(c.case_id)
                    governance_violations += len(getattr(rep, "violations", []))
                except Exception:  # noqa: BLE001 - never let diagnostics crash
                    continue

        warnings: list[str] = []
        if ocr_failures:
            warnings.append(f"{ocr_failures} OCR failure(s) recorded.")
        if low_conf:
            warnings.append(f"{low_conf} low-confidence OCR page event(s).")
        if claude_fallbacks:
            warnings.append(
                f"{claude_fallbacks} AI fallback(s) to the deterministic engine "
                f"({claude_fallback_rate:.0%} of AI operations)."
            )
        if governance_violations:
            warnings.append(
                f"{governance_violations} governance violation(s) across cases."
            )
        if cases_with_conflict:
            warnings.append(
                f"{cases_with_conflict} case(s) with unresolved cross-document conflicts."
            )

        return OperationalHealthReport(
            total_cases=total_cases,
            total_documents=documents_processed,
            ocr_failures=ocr_failures,
            extraction_failures=extraction_failures,
            review_failures=review_failures,
            appeal_failures=appeal_failures,
            claude_fallbacks=claude_fallbacks,
            governance_violations=governance_violations,
            conflicts_detected=cases_with_conflict,
            claude_fallback_rate=claude_fallback_rate,
            conflict_frequency=conflict_frequency,
            warnings=warnings,
        )

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
