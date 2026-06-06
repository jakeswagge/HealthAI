"""Shared UI helpers: CaseService access, metrics, and the persistence bridge.

These are imported by every tab module and by ``app/ui/case_ui.py`` (which
re-exports them for backward compatibility). Moved here verbatim from the old
monolithic ``case_ui.py`` during the Milestone 12 UI split - behavior unchanged.
"""

from __future__ import annotations

import threading

import streamlit as st

from app.cases.service import CaseService
from app.metrics.collector import MetricsCollector
from app.ui import session


_SERVICE_LOCAL = threading.local()


def get_case_service() -> CaseService:
    """Return a CaseService whose SQLite connection belongs to this thread."""
    service = getattr(_SERVICE_LOCAL, "case_service", None)
    if service is None:
        service = CaseService()
        _SERVICE_LOCAL.case_service = service
    return service


def get_metrics_collector() -> MetricsCollector:
    """A metrics collector sharing the service's connection."""
    service = get_case_service()
    return MetricsCollector(conn=service.conn)


# --------------------------------------------------------------------------- #
# Persistence bridge: save the current session's artifacts as a case
# --------------------------------------------------------------------------- #
def persist_current_case() -> str | None:
    """Persist the session's case/review/appeal into a CaseRecord.

    Idempotent per document: creates the case once (tracked via session), then
    attaches whichever artifacts are present. Returns the case id or None when
    there is nothing to persist yet.
    """
    case = session.get_case()
    if case is None:
        return None

    service = get_case_service()
    case_id = session.get_persisted_case_id()

    if case_id is None or service.get_case(case_id) is None:
        record = service.create_case(
            st.session_state.get(session.KEY_FILENAME)
        )
        case_id = record.case_id
        session.set_persisted_case_id(case_id)

    record = service.get_case(case_id)

    # Attach extraction (NEW -> EXTRACTED) if not already attached.
    if record.patient_case is None:
        service.attach_extraction(case_id, case)

    # Attach review if available and not already attached.
    review = session.get_review()
    if review is not None and service.get_case(case_id).review_result is None:
        service.attach_review(case_id, review)

    # Attach appeal if available and not already attached.
    appeal = session.get_appeal()
    if appeal is not None and service.get_case(case_id).appeal_letter is None:
        service.attach_appeal(case_id, appeal)

    return case_id


def select_or_create_case(service: CaseService, key_prefix: str = "assembly") -> str | None:
    """Let the user pick an existing case or start a new multi-document case."""
    cases = service.list_cases()
    labels = ["(new case)"] + [c.case_id for c in cases]
    choice = st.selectbox("Target case", labels, key=f"{key_prefix}_case_select")
    if choice == "(new case)":
        if st.button("Create new multi-document case", key=f"{key_prefix}_new_case"):
            rec = service.create_case(source_filename="multi-document case")
            session.set_persisted_case_id(rec.case_id)
            st.success(f"Created case {rec.case_id}.")
            return rec.case_id
        return session.get_persisted_case_id()
    session.set_persisted_case_id(choice)
    return choice


# Backwards-compatible private alias (the old module exposed ``_select_or_create_case``).
_select_or_create_case = select_or_create_case
