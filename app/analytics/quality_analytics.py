"""QualityAnalyticsEngine: evidence-quality + workflow analytics.

Computes, from the local SQLite stores:

- evidence approval / rejection / flag rates (from reviewer decisions)
- average quality score + weak evidence rate (from quality assessments)
- conflict rate (share of cases with at least one detected conflict)
- review turnaround time (case created -> human review completed, seconds)
- appeal generation success rate (cases with an appeal / cases reviewed)

All read-only and on demand; no external systems.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.models.audit_event import AuditEventType
from app.models.evidence_review_decision import EvidenceDecision
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


def _parse_ts(value: str):
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


@dataclass
class QualityAnalytics:
    """Snapshot of quality + workflow analytics."""

    total_cases: int = 0
    total_evidence: int = 0
    evidence_decisions: int = 0
    evidence_approval_rate: float = 0.0
    evidence_rejection_rate: float = 0.0
    evidence_flag_rate: float = 0.0
    average_quality_score: float = 0.0
    weak_evidence_rate: float = 0.0
    conflict_rate: float = 0.0
    review_turnaround_seconds: float = 0.0
    appeal_generation_success_rate: float = 0.0

    def as_dict(self) -> dict:
        return {
            "total_cases": self.total_cases,
            "total_evidence": self.total_evidence,
            "evidence_decisions": self.evidence_decisions,
            "evidence_approval_rate": self.evidence_approval_rate,
            "evidence_rejection_rate": self.evidence_rejection_rate,
            "evidence_flag_rate": self.evidence_flag_rate,
            "average_quality_score": self.average_quality_score,
            "weak_evidence_rate": self.weak_evidence_rate,
            "conflict_rate": self.conflict_rate,
            "review_turnaround_seconds": self.review_turnaround_seconds,
            "appeal_generation_success_rate": self.appeal_generation_success_rate,
        }


class QualityAnalyticsEngine:
    """Compute :class:`QualityAnalytics` from local storage."""

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)
        # Lazy imports avoid a circular dependency (cases package imports
        # service, which imports analytics).
        from app.audit.repository import AuditRepository
        from app.cases.repository import CaseRepository
        from app.cases.document_repository import CaseDocumentRepository
        from app.assembly.engine import CaseAssemblyEngine
        from app.quality.repository import EvidenceQualityRepository
        from app.quality.decision_repository import EvidenceReviewDecisionRepository

        self.cases = CaseRepository(conn=self.conn)
        self.documents = CaseDocumentRepository(conn=self.conn)
        self.audit = AuditRepository(conn=self.conn)
        self.quality = EvidenceQualityRepository(conn=self.conn)
        self.decisions = EvidenceReviewDecisionRepository(conn=self.conn)
        self.assembly = CaseAssemblyEngine()

    def collect(self) -> QualityAnalytics:
        cases = self.cases.all()
        total_cases = len(cases)

        # --- evidence decision rates (latest decision per evidence) ---
        latest_by_ev: dict[str, EvidenceDecision] = {}
        for c in cases:
            for d in self.decisions.for_case(c.case_id):
                latest_by_ev[d.evidence_id] = d.decision
        decided = len(latest_by_ev)
        approved = sum(1 for d in latest_by_ev.values() if d is EvidenceDecision.APPROVE)
        rejected = sum(1 for d in latest_by_ev.values() if d is EvidenceDecision.REJECT)
        flagged = sum(1 for d in latest_by_ev.values() if d is EvidenceDecision.FLAG)

        approval_rate = round(approved / decided, 4) if decided else 0.0
        rejection_rate = round(rejected / decided, 4) if decided else 0.0
        flag_rate = round(flagged / decided, 4) if decided else 0.0

        # --- quality scores ---
        all_quality = []
        total_evidence = 0
        for c in cases:
            qs = self.quality.for_case(c.case_id)
            all_quality.extend(qs)
        if all_quality:
            average_quality = round(
                sum(q.overall_score for q in all_quality) / len(all_quality), 4
            )
            weak_rate = round(
                sum(1 for q in all_quality if q.is_weak) / len(all_quality), 4
            )
        else:
            average_quality = 0.0
            weak_rate = 0.0

        # Total evidence count.
        for c in cases:
            total_evidence += self._evidence_count(c.case_id)

        # --- conflict rate (cases with >=1 detected conflict) ---
        cases_with_conflict = 0
        for c in cases:
            docs = self.documents.for_case(c.case_id)
            if not docs:
                continue
            report = self.assembly.assemble(c.case_id, docs).conflict_report
            if report.has_conflicts:
                cases_with_conflict += 1
        conflict_rate = round(cases_with_conflict / total_cases, 4) if total_cases else 0.0

        # --- review turnaround (created -> human review completed) ---
        turnarounds: list[float] = []
        for c in cases:
            events = self.audit.for_case(c.case_id)
            created = next((e for e in events if e.event_type is AuditEventType.CASE_CREATED), None)
            reviewed = next(
                (e for e in events if e.event_type is AuditEventType.HUMAN_REVIEW_COMPLETED),
                None,
            )
            if created and reviewed:
                t0, t1 = _parse_ts(created.timestamp), _parse_ts(reviewed.timestamp)
                if t0 and t1 and t1 >= t0:
                    turnarounds.append((t1 - t0).total_seconds())
        review_turnaround = round(sum(turnarounds) / len(turnarounds), 4) if turnarounds else 0.0

        # --- appeal generation success rate (appeals / reviewed cases) ---
        reviewed_cases = sum(
            1 for c in cases if c.review_result is not None
        )
        appeal_cases = sum(1 for c in cases if c.appeal_letter is not None)
        appeal_success = round(appeal_cases / reviewed_cases, 4) if reviewed_cases else 0.0

        return QualityAnalytics(
            total_cases=total_cases,
            total_evidence=total_evidence,
            evidence_decisions=decided,
            evidence_approval_rate=approval_rate,
            evidence_rejection_rate=rejection_rate,
            evidence_flag_rate=flag_rate,
            average_quality_score=average_quality,
            weak_evidence_rate=weak_rate,
            conflict_rate=conflict_rate,
            review_turnaround_seconds=review_turnaround,
            appeal_generation_success_rate=appeal_success,
        )

    def _evidence_count(self, case_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM evidence_references WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        return int(row["c"])

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
