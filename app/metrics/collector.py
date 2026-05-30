"""Operational metrics collection (local, derived from SQLite).

Computes lightweight workflow metrics by querying the cases and audit tables:

- documents_processed
- appeals_generated
- human_reviews_completed
- approval_rate
- rejection_rate
- average_processing_time
- fallback_rate (share of cases whose latest human decision was REQUEST_CHANGES)

These are simple counts/ratios, computed on demand. No background collection,
no external systems.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.audit.repository import AuditRepository
from app.cases.repository import CaseRepository
from app.models.audit_event import AuditEventType
from app.models.case_record import CaseStatus, HumanDecision
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


@dataclass
class OperationalMetrics:
    """A snapshot of operational metrics."""

    documents_processed: int = 0
    appeals_generated: int = 0
    human_reviews_completed: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    approval_rate: float = 0.0
    rejection_rate: float = 0.0
    average_processing_time: float = 0.0
    fallback_rate: float = 0.0
    total_cases: int = 0
    status_breakdown: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "documents_processed": self.documents_processed,
            "appeals_generated": self.appeals_generated,
            "human_reviews_completed": self.human_reviews_completed,
            "approved_count": self.approved_count,
            "rejected_count": self.rejected_count,
            "approval_rate": self.approval_rate,
            "rejection_rate": self.rejection_rate,
            "average_processing_time": self.average_processing_time,
            "fallback_rate": self.fallback_rate,
            "total_cases": self.total_cases,
            "status_breakdown": dict(self.status_breakdown),
        }


class MetricsCollector:
    """Compute :class:`OperationalMetrics` from local storage on demand."""

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)
        self.cases = CaseRepository(conn=self.conn)
        self.audit = AuditRepository(conn=self.conn)

    def collect(self) -> OperationalMetrics:
        """Compute and return the current metrics snapshot."""
        cases = self.cases.all()
        total = len(cases)

        documents_processed = len(
            self.audit.by_type(AuditEventType.DOCUMENT_UPLOADED)
        )
        appeals_generated = len(
            self.audit.by_type(AuditEventType.APPEAL_GENERATED)
        )
        human_reviews_completed = len(
            self.audit.by_type(AuditEventType.HUMAN_REVIEW_COMPLETED)
        )

        # Decisions are derived from each case's latest human decision so that
        # rates reflect current case state, not historical churn.
        approved = 0
        rejected = 0
        request_changes = 0
        decided = 0
        for c in cases:
            latest = c.latest_decision()
            if latest is None:
                continue
            decided += 1
            if latest.decision is HumanDecision.APPROVE:
                approved += 1
            elif latest.decision is HumanDecision.REJECT:
                rejected += 1
            elif latest.decision is HumanDecision.REQUEST_CHANGES:
                request_changes += 1

        approval_rate = round(approved / decided, 4) if decided else 0.0
        rejection_rate = round(rejected / decided, 4) if decided else 0.0
        fallback_rate = round(request_changes / decided, 4) if decided else 0.0

        # Average processing time across cases that recorded one.
        times = [c.processing_seconds for c in cases if c.processing_seconds]
        average_processing_time = round(sum(times) / len(times), 4) if times else 0.0

        # Status breakdown.
        breakdown: dict[str, int] = {s.value: 0 for s in CaseStatus}
        for c in cases:
            breakdown[c.status.value] = breakdown.get(c.status.value, 0) + 1

        return OperationalMetrics(
            documents_processed=documents_processed,
            appeals_generated=appeals_generated,
            human_reviews_completed=human_reviews_completed,
            approved_count=approved,
            rejected_count=rejected,
            approval_rate=approval_rate,
            rejection_rate=rejection_rate,
            average_processing_time=average_processing_time,
            fallback_rate=fallback_rate,
            total_cases=total,
            status_breakdown=breakdown,
        )

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
