"""Case lifecycle tabs: Case Management, Human Review, Audit Log, Metrics.

Moved verbatim from the old ``case_ui.py`` during the Milestone 12 UI split.
"""

from __future__ import annotations

import streamlit as st

from app.cases.export import build_export_zip
from app.cases.service import CaseService
from app.models.case_record import CaseStatus, HumanDecision
from app.ui import session
from app.ui.tabs.common import (
    get_case_service,
    get_metrics_collector,
    persist_current_case,
)


# --------------------------------------------------------------------------- #
# Tab: Case Management
# --------------------------------------------------------------------------- #
def render_case_management_tab() -> None:
    st.caption(
        "Track every prior-authorization case through its lifecycle. Cases are "
        "persisted locally in SQLite."
    )

    service = get_case_service()

    # Offer to save the current in-progress work as a case.
    if session.get_case() is not None:
        persisted_id = session.get_persisted_case_id()
        col1, col2 = st.columns([3, 1])
        col1.info(
            "There is an extracted case in the current session"
            + (f" (saved as {persisted_id})." if persisted_id else " not yet saved.")
        )
        if col2.button("Save / update case", key="save_case"):
            cid = persist_current_case()
            if cid:
                st.success(f"Saved case {cid}.")

    cases = service.list_cases()
    if not cases:
        st.info("No cases yet. Process a document and click **Save / update case**.")
        return

    st.markdown(f"### All cases ({len(cases)})")
    rows = [
        {
            "case_id": c.case_id,
            "patient": (c.patient_case.patient_name if c.patient_case else "—") or "—",
            "service": (c.patient_case.requested_service if c.patient_case else "—") or "—",
            "status": c.status.value,
            "reviewer": c.assigned_reviewer or "—",
            "updated_at": c.updated_at,
        }
        for c in cases
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    case_ids = [c.case_id for c in cases]
    selected = st.selectbox("Open case", case_ids, key="cm_select")
    if selected:
        _render_case_detail(service, selected)


def _render_case_detail(service: CaseService, case_id: str) -> None:
    record = service.get_case(case_id)
    if record is None:
        st.error("Case not found.")
        return

    st.markdown(f"#### {record.display_name()}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Status", record.status.value)
    c2.metric("Reviewer", record.assigned_reviewer or "unassigned")
    c3.metric("Decisions", len(record.review_decisions))

    if record.patient_case:
        with st.expander("Patient case"):
            st.json(record.patient_case.model_dump(mode="json"))
    if record.review_result:
        with st.expander("Review result"):
            st.json(record.review_result.model_dump(mode="json"))
    if record.appeal_letter:
        with st.expander("Appeal letter"):
            st.markdown(record.appeal_letter.letter_text)

    # Export package (includes evidence + conflicts + M8 data when available).
    events = service.history(case_id)
    evidence = service.evidence.for_case(case_id)
    conflict_report = None
    if evidence:
        docs = service.list_documents(case_id)
        if docs:
            conflict_report = service.assembly.assemble(case_id, docs).conflict_report
    authoritative_facts = service.list_authoritative_facts(case_id)
    resolutions = service.list_resolutions(case_id)
    feedback = service.list_feedback(case_id)
    ocr_results = service.list_ocr_results(case_id)
    evidence_quality = service.list_evidence_quality(case_id)
    evidence_decisions = service.list_evidence_decisions(case_id)
    # Milestone 11: governance-filtered set, compliance, analytics.
    settings = service.get_governance_settings()
    approved_set = service.build_approved_evidence_set(case_id, settings)
    governance_report = service.check_compliance(case_id, settings)
    analytics = service.quality_analytics().as_dict()
    # Milestone 13: explainability + traceability chain (governance-aware).
    traceability_chain = service.traceability_chain(case_id, settings)
    review_explanation = (
        service.explain_review(case_id, record.review_result, settings)
        if record.review_result
        else None
    )
    appeal_explanation = (
        service.explain_appeal(case_id, record.appeal_letter, settings)
        if record.appeal_letter
        else None
    )
    # Final Milestone: payer profile + operational health.
    payer_profile = service.get_payer(
        record.review_result.payer_id if record.review_result else None
    )
    operational_health = service.operational_health()
    zip_bytes = build_export_zip(
        record,
        events,
        evidence=evidence or None,
        conflict_report=conflict_report,
        authoritative_facts=authoritative_facts or None,
        conflict_resolutions=resolutions or None,
        reviewer_feedback=feedback or None,
        ocr_results=ocr_results or None,
        evidence_quality=evidence_quality or None,
        evidence_review_decisions=evidence_decisions or None,
        governance_report=governance_report,
        quality_analytics=analytics,
        approved_evidence_set=approved_set,
        all_evidence=evidence or None,
        review_explanation=review_explanation,
        appeal_explanation=appeal_explanation,
        traceability_chain=traceability_chain,
        payer_profile=payer_profile,
        operational_health=operational_health,
    )
    if st.download_button(
        "Download export package (ZIP)",
        data=zip_bytes,
        file_name=f"{case_id}_export.zip",
        mime="application/zip",
        key=f"export_{case_id}",
    ):
        service.mark_exported(case_id)


# --------------------------------------------------------------------------- #
# Tab: Human Review
# --------------------------------------------------------------------------- #
def render_human_review_tab() -> None:
    st.caption(
        "Review generated appeals and record a decision. Cases awaiting review "
        "are listed below."
    )

    service = get_case_service()
    pending = service.cases.by_status(CaseStatus.PENDING_HUMAN_REVIEW)

    if not pending:
        st.info("No cases are pending human review.")
        return

    case_ids = [c.case_id for c in pending]
    selected = st.selectbox("Case to review", case_ids, key="hr_select")
    record = service.get_case(selected)
    if record is None:
        return

    st.markdown(f"#### {record.display_name()}")
    if record.review_result:
        rec = record.review_result.recommendation.value
        st.markdown(f"**System recommendation:** {rec}")
    if record.appeal_letter:
        with st.expander("Generated appeal letter", expanded=True):
            st.markdown(record.appeal_letter.letter_text)

    with st.form(key=f"review_form_{selected}"):
        reviewer = st.text_input("Reviewer name", value=record.assigned_reviewer or "")
        decision = st.radio(
            "Decision",
            options=[d.value for d in HumanDecision],
            horizontal=True,
        )
        comments = st.text_area("Comments")
        submitted = st.form_submit_button("Record decision", type="primary")

    if submitted:
        if not reviewer.strip():
            st.error("Reviewer name is required.")
            return
        service.record_human_review(selected, reviewer.strip(), decision, comments)
        updated = service.get_case(selected)
        st.success(
            f"Recorded {decision} for {selected}. New status: {updated.status.value}."
        )


# --------------------------------------------------------------------------- #
# Tab: Audit Log
# --------------------------------------------------------------------------- #
def render_audit_log_tab() -> None:
    st.caption("Immutable audit trail of all significant actions.")

    service = get_case_service()
    cases = service.list_cases()
    options = ["(all cases)"] + [c.case_id for c in cases]
    selected = st.selectbox("Filter by case", options, key="audit_select")

    if selected == "(all cases)":
        events = service.audit.all(limit=500)
    else:
        events = service.history(selected)

    if not events:
        st.info("No audit events recorded yet.")
        return

    rows = [
        {
            "timestamp": e.timestamp,
            "case_id": e.case_id,
            "event_type": e.event_type.value,
            "actor": e.actor.value,
            "details": e.details,
        }
        for e in events
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption(f"{len(events)} event(s).")


# --------------------------------------------------------------------------- #
# Tab: Operational Metrics
# --------------------------------------------------------------------------- #
def render_metrics_tab() -> None:
    st.caption("Lightweight operational metrics, computed on demand from local storage.")

    metrics = get_metrics_collector().collect()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Documents processed", metrics.documents_processed)
    c2.metric("Appeals generated", metrics.appeals_generated)
    c3.metric("Human reviews", metrics.human_reviews_completed)
    c4.metric("Total cases", metrics.total_cases)

    c5, c6, c7 = st.columns(3)
    c5.metric("Approval rate", f"{metrics.approval_rate:.0%}")
    c6.metric("Rejection rate", f"{metrics.rejection_rate:.0%}")
    c7.metric("Fallback rate", f"{metrics.fallback_rate:.0%}")

    st.metric("Avg processing time (s)", metrics.average_processing_time)

    st.markdown("#### Status breakdown")
    breakdown = {k: v for k, v in metrics.status_breakdown.items() if v}
    if breakdown:
        st.bar_chart(breakdown)
    else:
        st.caption("No cases yet.")

    with st.expander("Raw metrics JSON"):
        st.json(metrics.as_dict())
