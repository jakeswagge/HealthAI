"""Streamlit UI for Milestone 5: case management, human review, audit, metrics.

These render functions are imported by ``dashboard.py`` and added as tabs. They
use a single file-backed :class:`CaseService` (cached as a Streamlit resource)
so the workflow persists across reruns and sessions.

The case-management layer is independent of the extraction/review/appeal
engines; this UI only persists the artifacts those engines already produced
(held in session state) and drives the workflow + audit trail.
"""

from __future__ import annotations

import streamlit as st

from app.cases.export import build_export_zip
from app.cases.service import CaseService
from app.feedback.dataset import FeedbackDataset
from app.metrics.collector import MetricsCollector
from app.models.case_record import CaseStatus, HumanDecision
from app.models.case_document import DocumentCategory
from app.models.conflict_report import ConflictSeverity
from app.models.ocr_result import DEFAULT_OCR_CONFIDENCE_THRESHOLD
from app.models.reviewer_feedback import FeedbackTarget, FeedbackVerdict
from app.models.evidence_review_decision import EvidenceDecision
from app.models.governance import GovernanceSettings
from app.extraction.extractor import extract_text_from_bytes
from app.ui import session


@st.cache_resource(show_spinner=False)
def get_case_service() -> CaseService:
    """Return a process-wide CaseService backed by the default SQLite file."""
    return CaseService()


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


# --------------------------------------------------------------------------- #
# Tab: Document Assembly (Milestone 6/7)
# --------------------------------------------------------------------------- #
def _select_or_create_case(service: CaseService) -> str | None:
    """Let the user pick an existing case or start a new multi-document case."""
    cases = service.list_cases()
    labels = ["(new case)"] + [c.case_id for c in cases]
    choice = st.selectbox("Target case", labels, key="assembly_case_select")
    if choice == "(new case)":
        if st.button("Create new multi-document case", key="assembly_new_case"):
            rec = service.create_case(source_filename="multi-document case")
            session.set_persisted_case_id(rec.case_id)
            st.success(f"Created case {rec.case_id}.")
            return rec.case_id
        return session.get_persisted_case_id()
    return choice


def render_document_assembly_tab() -> None:
    st.caption(
        "Attach multiple supporting documents to a case (denial letter, "
        "clinical notes, labs, imaging, ...) and assemble a unified, "
        "evidence-backed view."
    )

    service = get_case_service()
    case_id = _select_or_create_case(service)
    if not case_id:
        st.info("Create or select a case to begin assembling documents.")
        return

    st.markdown(f"#### Documents for `{case_id}`")
    existing = service.list_documents(case_id)
    if existing:
        st.dataframe(
            [
                {
                    "filename": d.filename,
                    "type": d.document_type.value,
                    "pages": d.page_count,
                    "chars": d.char_count,
                }
                for d in existing
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No documents attached yet.")

    # Upload + classify.
    uploads = st.file_uploader(
        "Add supporting documents",
        type=["pdf", "txt"],
        accept_multiple_files=True,
        key="assembly_uploader",
    )
    type_options = ["(auto-detect)"] + [c.value for c in DocumentCategory]
    chosen_type = st.selectbox("Document type", type_options, key="assembly_doc_type")

    if uploads and st.button("Attach uploaded document(s)", key="assembly_attach"):
        added = 0
        for up in uploads:
            try:
                extracted = extract_text_from_bytes(up.name, up.getvalue())
            except Exception as exc:  # noqa: BLE001
                st.error(f"{up.name}: {exc}")
                continue
            dt = None if chosen_type == "(auto-detect)" else chosen_type
            service.add_document(
                case_id, up.name, extracted.text, extracted.page_count, dt
            )
            added += 1
        if added:
            st.success(f"Attached {added} document(s).")
            st.rerun()

    # Assemble.
    if service.list_documents(case_id):
        if st.button("Assemble case", type="primary", key="assembly_run"):
            ctx = service.assemble_case(case_id)
            st.success(
                f"Assembled {len(ctx.document_ids)} document(s): "
                f"{len(ctx.evidence)} evidence references, "
                f"{len(ctx.conflict_report.conflicts)} conflict(s)."
            )
            with st.expander("Synthesized patient case", expanded=True):
                st.json(ctx.patient_case.model_dump(mode="json"))
            if ctx.missing_information:
                st.warning("Missing information:")
                for m in ctx.missing_information:
                    st.markdown(f"- {m}")


# --------------------------------------------------------------------------- #
# Tab: Evidence Explorer (Milestone 6/7)
# --------------------------------------------------------------------------- #
def render_evidence_explorer_tab() -> None:
    st.caption(
        "Browse the source-backed evidence inventory. Every fact is traceable "
        "to a document and page."
    )

    service = get_case_service()
    cases_with_evidence = [
        c.case_id for c in service.list_cases()
        if service.evidence.count_for_case(c.case_id) > 0
    ]
    if not cases_with_evidence:
        st.info("No assembled evidence yet. Assemble a case in Document Assembly.")
        return

    selected = st.selectbox("Case", cases_with_evidence, key="evidence_case_select")
    evidence = service.evidence.for_case(selected)

    # Filter by fact type.
    fact_types = sorted({e.fact_type or "unknown" for e in evidence})
    chosen = st.multiselect("Filter by fact type", fact_types, default=fact_types, key="evidence_filter")
    filtered = [e for e in evidence if (e.fact_type or "unknown") in chosen]

    st.markdown(f"#### {len(filtered)} evidence reference(s)")
    st.dataframe(
        [
            {
                "fact": e.fact_type,
                "value": e.normalized_fact.split(": ", 1)[-1],
                "source": e.source_filename,
                "page": e.page_number,
                "section": e.section_label or "—",
                "confidence": f"{e.confidence_score:.0%}",
                "evidence_id": e.evidence_id,
            }
            for e in filtered
        ],
        use_container_width=True,
        hide_index=True,
    )

    # Inspect a single reference verbatim.
    ids = [e.evidence_id for e in filtered]
    if ids:
        ev_id = st.selectbox("Inspect source reference", ids, key="evidence_inspect")
        ref = service.evidence.get(ev_id)
        if ref:
            st.markdown(f"**{ref.normalized_fact}** {ref.citation()}")
            st.markdown(f"Section: {ref.section_label or '—'}")
            st.code(ref.quoted_text or "(no quote captured)", language="text")


# --------------------------------------------------------------------------- #
# Tab: Conflict Review (Milestone 6/7)
# --------------------------------------------------------------------------- #
_SEVERITY_RENDER = {
    ConflictSeverity.HIGH: ("🔴 HIGH", st.error),
    ConflictSeverity.MEDIUM: ("🟠 MEDIUM", st.warning),
    ConflictSeverity.LOW: ("🟡 LOW", st.info),
}


def render_conflict_review_tab() -> None:
    st.caption(
        "Inspect cross-document conflicts (e.g. different diagnoses or member "
        "IDs) detected during assembly, with severity levels."
    )

    service = get_case_service()
    cases_with_docs = [
        c.case_id for c in service.list_cases()
        if service.documents.count_for_case(c.case_id) > 0
    ]
    if not cases_with_docs:
        st.info("No multi-document cases yet. Assemble a case in Document Assembly.")
        return

    selected = st.selectbox("Case", cases_with_docs, key="conflict_case_select")
    docs = service.list_documents(selected)
    report = service.assembly.assemble(selected, docs).conflict_report

    if not report.has_conflicts:
        st.success("No conflicts detected across the documents in this case.")
        return

    st.markdown(f"#### {len(report.conflicts)} conflict(s) detected")
    st.caption(f"Highest severity: {report.highest_severity.value}")
    for conflict in report.conflicts:
        label, renderer = _SEVERITY_RENDER.get(
            conflict.severity, ("UNKNOWN", st.info)
        )
        renderer(f"{label} — **{conflict.fact_type}**")
        st.markdown(conflict.description)
        for value in conflict.values:
            st.markdown(f"- {value}")
        st.divider()


# --------------------------------------------------------------------------- #
# Tab: Conflict Resolution (Milestone 8)
# --------------------------------------------------------------------------- #
def _strip_citation(value: str) -> str:
    """Remove a trailing '(file, p.N)' citation from a displayed value."""
    idx = value.rfind(" (")
    return value[:idx].strip() if idx != -1 else value.strip()


def render_conflict_resolution_tab() -> None:
    st.caption(
        "Human reviewers resolve conflicting evidence and establish the "
        "authoritative case record. No conflict is resolved automatically; "
        "rejected values are always preserved and every decision is audited."
    )

    service = get_case_service()
    cases_with_docs = [
        c.case_id for c in service.list_cases()
        if service.documents.count_for_case(c.case_id) > 0
    ]
    if not cases_with_docs:
        st.info("No multi-document cases yet. Assemble a case in Document Assembly.")
        return

    selected = st.selectbox("Case", cases_with_docs, key="resolution_case_select")
    docs = service.list_documents(selected)
    report = service.assembly.assemble(selected, docs).conflict_report

    # Show current authoritative facts.
    with st.expander("Current authoritative facts"):
        facts = service.list_authoritative_facts(selected)
        if facts:
            st.dataframe(
                [
                    {
                        "fact": f.fact_type,
                        "value": f.value,
                        "source": f.resolution_source.value,
                        "document": f.source_document or "—",
                        "confidence": f"{f.confidence:.0%}",
                    }
                    for f in facts
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No authoritative facts yet (assemble the case first).")

    if not report.has_conflicts:
        st.success("No unresolved conflicts detected for this case.")
    else:
        st.markdown(f"#### {len(report.conflicts)} conflict(s) to resolve")
        for conflict in report.conflicts:
            with st.form(key=f"resolve_{conflict.conflict_id}"):
                st.markdown(
                    f"**{conflict.fact_type}** — severity {conflict.severity.value}"
                )
                st.caption(conflict.description)
                # Options are the cited conflicting values; store the clean value.
                display_values = conflict.values
                choice = st.radio(
                    "Authoritative value",
                    options=display_values,
                    key=f"choice_{conflict.conflict_id}",
                )
                reviewer = st.text_input(
                    "Reviewer name", key=f"rev_{conflict.conflict_id}"
                )
                justification = st.text_area(
                    "Justification", key=f"just_{conflict.conflict_id}"
                )
                submitted = st.form_submit_button("Submit resolution", type="primary")

            if submitted:
                if not reviewer.strip():
                    st.error("Reviewer name is required.")
                    continue
                chosen = _strip_citation(choice)
                rejected = [
                    _strip_citation(v) for v in display_values if v != choice
                ]
                service.resolve_conflict(
                    selected,
                    conflict.conflict_id,
                    conflict.fact_type,
                    chosen_value=chosen,
                    rejected_values=rejected,
                    reviewer_name=reviewer.strip(),
                    justification=justification,
                )
                st.success(
                    f"Resolved '{conflict.fact_type}' -> '{chosen}'. "
                    "Authoritative record updated; downstream review/appeal will "
                    "use this value. (rejected values preserved)."
                )
                st.rerun()

    # Resolution history.
    resolutions = service.list_resolutions(selected)
    if resolutions:
        st.markdown("#### Resolution history")
        st.dataframe(
            [
                {
                    "fact": r.fact_type,
                    "chosen": r.chosen_value,
                    "rejected": ", ".join(r.rejected_values),
                    "reviewer": r.reviewer_name,
                    "justification": r.justification,
                    "timestamp": r.timestamp,
                }
                for r in resolutions
            ],
            use_container_width=True,
            hide_index=True,
        )


# --------------------------------------------------------------------------- #
# Tab: Reviewer Feedback (Milestone 8)
# --------------------------------------------------------------------------- #
def render_reviewer_feedback_tab() -> None:
    st.caption(
        "Capture structured reviewer feedback on each pipeline stage. This is "
        "collected as learning data only — no model is retrained."
    )

    service = get_case_service()
    cases = service.list_cases()
    if not cases:
        st.info("No cases yet.")
        return

    selected = st.selectbox(
        "Case", [c.case_id for c in cases], key="feedback_case_select"
    )

    with st.form(key="feedback_form"):
        reviewer = st.text_input("Reviewer name")
        target = st.selectbox(
            "Target", [t.value for t in FeedbackTarget], key="feedback_target"
        )
        verdict = st.radio(
            "Verdict",
            options=[v.value for v in FeedbackVerdict],
            horizontal=True,
            key="feedback_verdict",
        )
        comments = st.text_area("Comments")
        submitted = st.form_submit_button("Submit feedback", type="primary")

    if submitted:
        if not reviewer.strip():
            st.error("Reviewer name is required.")
        else:
            service.record_reviewer_feedback(
                selected, reviewer.strip(), target, verdict, comments=comments
            )
            st.success(f"Recorded {verdict} feedback on {target} for {selected}.")
            st.rerun()

    # Feedback history.
    feedback = service.list_feedback(selected)
    st.markdown("#### Feedback history")
    if feedback:
        st.dataframe(
            [
                {
                    "target": f.target_type.value,
                    "verdict": f.feedback.value,
                    "reviewer": f.reviewer,
                    "comments": f.comments,
                    "timestamp": f.timestamp,
                }
                for f in feedback
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No feedback recorded for this case yet.")

    # Learning dataset export.
    st.markdown("#### Learning dataset")
    st.caption(
        "Structured corrections + resolutions + feedback, exportable for offline "
        "analysis. No machine learning is performed."
    )
    dataset = FeedbackDataset(
        feedback_repo=service.feedback,
        resolution_repo=service.resolutions,
        facts_repo=service.authoritative_facts,
    )
    st.download_button(
        "Download learning dataset (JSON)",
        data=dataset.export_json(selected),
        file_name=f"{selected}_learning_dataset.json",
        mime="application/json",
        key="feedback_dataset_download",
    )


# --------------------------------------------------------------------------- #
# Tab: Document Ingestion (Milestone 9)
# --------------------------------------------------------------------------- #
_INGEST_TYPES = ["txt", "pdf", "png", "jpg", "jpeg"]


def render_document_ingestion_tab() -> None:
    st.caption(
        "Upload scanned PDFs, faxes, or images (PNG/JPG/JPEG). HealthAI detects "
        "whether a text layer exists and runs OCR only when needed. OCR "
        "uncertainty is always shown; low-quality OCR is flagged, never "
        "silently accepted."
    )

    service = get_case_service()
    st.info(f"Active OCR provider: **{service.describe_ocr()}**")
    if "Mock" in service.describe_ocr():
        st.caption(
            "Tesseract is not installed, so a deterministic offline OCR stand-in "
            "is used (it decodes text fixtures). Install tesseract + "
            "`pip install pytesseract pillow` for real image OCR."
        )

    case_id = _select_or_create_case(service)
    if not case_id:
        st.info("Create or select a case to ingest documents.")
        return

    threshold = st.slider(
        "OCR confidence threshold (low-confidence pages are flagged)",
        min_value=0.0, max_value=1.0,
        value=float(DEFAULT_OCR_CONFIDENCE_THRESHOLD), step=0.05,
        key="ingest_threshold",
    )

    uploads = st.file_uploader(
        "Upload documents (TXT, PDF, PNG, JPG, JPEG)",
        type=_INGEST_TYPES,
        accept_multiple_files=True,
        key="ingest_uploader",
    )
    type_options = ["(auto-detect)"] + [c.value for c in DocumentCategory]
    override = st.selectbox("Document type", type_options, key="ingest_doc_type")

    if uploads and st.button("Ingest document(s)", type="primary", key="ingest_run"):
        for up in uploads:
            override_val = None if override == "(auto-detect)" else override
            doc, result = service.ingest_document(
                case_id, up.name, up.getvalue(),
                category_override=override_val,
                ocr_confidence_threshold=threshold,
            )
            method = (
                result.ocr_results[0].processing_method.value
                if result.ocr_results else "TEXT_LAYER"
            )
            msg = (
                f"Ingested **{up.name}** as {result.kind.value} "
                f"(type={doc.document_type.value}, pages={result.page_count}, "
                f"method={method})."
            )
            if not result.ocr_available:
                st.warning(msg + " OCR unavailable — no text extracted.")
            elif result.ocr_used and result.low_confidence_pages(threshold):
                st.warning(
                    msg + f" Low-confidence pages: {result.low_confidence_pages(threshold)} "
                    "— inspect source text in the OCR Explorer."
                )
            else:
                st.success(msg)
            for w in result.warnings:
                st.warning(w)
        st.rerun()

    docs = service.list_documents(case_id)
    if docs:
        st.markdown(f"#### Documents in `{case_id}`")
        st.dataframe(
            [
                {
                    "filename": d.filename,
                    "type": d.document_type.value,
                    "pages": d.page_count,
                    "ocr_pages": len(service.ocr_results_for_document(d.document_id)),
                }
                for d in docs
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.caption("Assemble the case in the Document Assembly tab to build evidence.")


# --------------------------------------------------------------------------- #
# Tab: OCR Explorer (Milestone 9)
# --------------------------------------------------------------------------- #
def render_ocr_explorer_tab() -> None:
    st.caption(
        "Inspect page-by-page OCR output, confidence, processing method, and "
        "the classified document type. Reviewers can read the exact source "
        "text behind every OCR-derived fact."
    )

    service = get_case_service()
    cases_with_ocr = [
        c.case_id for c in service.list_cases()
        if service.ocr_results.count_for_case(c.case_id) > 0
    ]
    if not cases_with_ocr:
        st.info("No OCR results yet. Ingest a scanned PDF or image first.")
        return

    selected = st.selectbox("Case", cases_with_ocr, key="ocr_case_select")
    threshold = st.slider(
        "Low-confidence threshold", 0.0, 1.0,
        float(DEFAULT_OCR_CONFIDENCE_THRESHOLD), 0.05, key="ocr_threshold",
    )

    docs = {d.document_id: d for d in service.list_documents(selected)}
    results = service.list_ocr_results(selected)

    # Group by document.
    by_doc: dict[str, list] = {}
    for r in results:
        by_doc.setdefault(r.document_id, []).append(r)

    for document_id, pages in by_doc.items():
        doc = docs.get(document_id)
        title = doc.filename if doc else document_id
        dtype = doc.document_type.value if doc else "?"
        st.markdown(f"#### {title}  —  classified: **{dtype}**")
        for page in sorted(pages, key=lambda p: p.page_number):
            low = page.confidence < threshold
            label = (
                f"Page {page.page_number} · {page.processing_method.value} · "
                f"confidence {page.confidence:.0%}"
            )
            if low:
                st.error(label + "  ⚠️ LOW CONFIDENCE")
            else:
                st.markdown(f"**{label}**")
            st.text_area(
                f"OCR text (page {page.page_number})",
                value=page.raw_text,
                height=160,
                key=f"ocrtext_{page.ocr_id}",
                label_visibility="collapsed",
            )
        st.divider()


# --------------------------------------------------------------------------- #
# Tab: Evidence Quality (Milestone 10)
# --------------------------------------------------------------------------- #
def render_evidence_quality_tab() -> None:
    st.caption(
        "Score the quality of extracted evidence (completeness, relevance, "
        "consistency, traceability) and surface weak or conflicting evidence. "
        "Optionally re-extract evidence with Claude (anti-fabrication gated)."
    )

    service = get_case_service()
    cases_with_evidence = [
        c.case_id for c in service.list_cases()
        if service.evidence.count_for_case(c.case_id) > 0
    ]
    if not cases_with_evidence:
        st.info("No assembled evidence yet. Assemble a case in Document Assembly.")
        return

    selected = st.selectbox("Case", cases_with_evidence, key="quality_case_select")

    if st.button("Score evidence quality", type="primary", key="quality_run"):
        assessments = service.score_evidence(selected)
        weak = sum(1 for a in assessments if a.is_weak)
        st.success(f"Scored {len(assessments)} evidence reference(s); {weak} weak.")

    assessments = service.list_evidence_quality(selected)
    if not assessments:
        st.info("Click **Score evidence quality** to assess this case's evidence.")
        return

    ev_by_id = {e.evidence_id: e for e in service.list_evidence(selected)}
    rows = []
    for a in assessments:
        ev = ev_by_id.get(a.evidence_id)
        rows.append(
            {
                "fact": (ev.fact_type if ev else "?"),
                "value": (ev.normalized_fact.split(": ", 1)[-1] if ev else ""),
                "overall": f"{a.overall_score:.0%}",
                "complete": f"{a.completeness_score:.0%}",
                "relevance": f"{a.relevance_score:.0%}",
                "consistency": f"{a.consistency_score:.0%}",
                "traceability": f"{a.traceability_score:.0%}",
                "weak": "⚠️" if a.is_weak else "",
                "issues": "; ".join(a.issues) or "—",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    weak = [a for a in assessments if a.is_weak]
    if weak:
        st.warning(f"{len(weak)} weak evidence reference(s) — review in the Reviewer Workbench.")


# --------------------------------------------------------------------------- #
# Tab: Reviewer Workbench (Milestone 10)
# --------------------------------------------------------------------------- #
_EV_DECISION_RENDER = {
    "APPROVE": st.success,
    "REJECT": st.error,
    "FLAG": st.warning,
    "PENDING": st.info,
}


def render_reviewer_workbench_tab() -> None:
    st.caption(
        "Validate evidence one item at a time: read the source quote, see "
        "quality scores and supporting/conflicting evidence, then approve, "
        "reject, or flag. Rejected evidence is excluded from downstream use."
    )

    service = get_case_service()
    cases_with_evidence = [
        c.case_id for c in service.list_cases()
        if service.evidence.count_for_case(c.case_id) > 0
    ]
    if not cases_with_evidence:
        st.info("No assembled evidence yet. Assemble a case first.")
        return

    selected = st.selectbox("Case", cases_with_evidence, key="wb_case_select")
    reviewer = st.text_input("Reviewer name", key="wb_reviewer")

    views = service.build_evidence_views(selected)
    if not views:
        st.info("No evidence to review.")
        return

    approved = len(service.workbench.approved_evidence_ids(selected))
    rejected = len(service.workbench.rejected_evidence_ids(selected))
    c1, c2, c3 = st.columns(3)
    c1.metric("Evidence", len(views))
    c2.metric("Approved", approved)
    c3.metric("Rejected", rejected)

    for view in views:
        ev = view.evidence
        value = ev.normalized_fact.split(": ", 1)[-1]
        renderer = _EV_DECISION_RENDER.get(view.status, st.info)
        renderer(f"**{ev.fact_type}**: {value}  —  status: {view.status}")
        st.markdown(f"Source: {ev.citation()}  ·  confidence {ev.confidence_score:.0%}")
        st.code(ev.quoted_text or "(no quote)", language="text")

        if view.quality:
            q = view.quality
            st.caption(
                f"Quality {q.overall_score:.0%} "
                f"(complete {q.completeness_score:.0%}, relevance {q.relevance_score:.0%}, "
                f"consistency {q.consistency_score:.0%}, traceability {q.traceability_score:.0%})"
                + (f" ⚠️ {'; '.join(q.issues)}" if q.issues else "")
            )
        if view.conflicting:
            st.caption("Conflicting evidence:")
            for c in view.conflicting:
                st.caption(f"  - {c.normalized_fact.split(': ', 1)[-1]} {c.citation()}")
        if view.supporting:
            st.caption(f"Supporting evidence: {len(view.supporting)} other reference(s) agree.")

        col_a, col_r, col_f = st.columns(3)
        if col_a.button("Approve", key=f"appr_{ev.evidence_id}"):
            _record_ev_decision(service, selected, ev.evidence_id, reviewer, EvidenceDecision.APPROVE)
        if col_r.button("Reject", key=f"rej_{ev.evidence_id}"):
            _record_ev_decision(service, selected, ev.evidence_id, reviewer, EvidenceDecision.REJECT)
        if col_f.button("Flag", key=f"flag_{ev.evidence_id}"):
            _record_ev_decision(service, selected, ev.evidence_id, reviewer, EvidenceDecision.FLAG)
        st.divider()

    # Decision history.
    decisions = service.list_evidence_decisions(selected)
    if decisions:
        st.markdown("#### Evidence decision history")
        st.dataframe(
            [
                {
                    "evidence_id": d.evidence_id,
                    "decision": d.decision.value,
                    "reviewer": d.reviewer,
                    "comments": d.comments,
                    "timestamp": d.timestamp,
                }
                for d in decisions
            ],
            use_container_width=True,
            hide_index=True,
        )


def _record_ev_decision(service, case_id, evidence_id, reviewer, decision) -> None:
    if not reviewer.strip():
        st.error("Enter a reviewer name before recording a decision.")
        return
    service.record_evidence_decision(case_id, evidence_id, reviewer.strip(), decision)
    st.rerun()


# --------------------------------------------------------------------------- #
# Tab: Governance Settings (Milestone 11)
# --------------------------------------------------------------------------- #
def render_governance_settings_tab() -> None:
    st.caption(
        "Configure how strictly evidence must be validated before review and "
        "appeal generation use it. Reviewer authority always wins: rejected "
        "evidence is never used in validated mode."
    )

    service = get_case_service()
    current = service.get_governance_settings()

    with st.form(key="governance_form"):
        validated = st.toggle(
            "Validated evidence mode",
            value=current.validated_evidence_mode,
            help="When on, downstream review/appeal use only governance-filtered evidence.",
        )
        allow_unreviewed = st.toggle(
            "Allow unreviewed evidence (validated mode)",
            value=current.allow_unreviewed_evidence,
            help="If off, only reviewer-APPROVED evidence is used in validated mode.",
        )
        min_quality = st.slider(
            "Minimum evidence quality score", 0.0, 1.0,
            float(current.minimum_quality_score), 0.05,
        )
        require_conflict = st.toggle(
            "Require conflict resolution", value=current.require_conflict_resolution
        )
        require_human = st.toggle(
            "Require human review before export",
            value=current.require_human_review_before_export,
        )
        reviewer = st.text_input("Your name (for the audit trail)", value="admin")
        submitted = st.form_submit_button("Save governance settings", type="primary")

    if submitted:
        service.update_governance_settings(
            GovernanceSettings(
                validated_evidence_mode=validated,
                allow_unreviewed_evidence=allow_unreviewed,
                minimum_quality_score=min_quality,
                require_conflict_resolution=require_conflict,
                require_human_review_before_export=require_human,
            ),
            actor=reviewer.strip() or "admin",
        )
        st.success("Governance settings saved (audited).")

    mode = "VALIDATED" if current.validated_evidence_mode else "DRAFT"
    st.info(f"Current mode: **{mode}**")

    # Per-case governance compliance + filtered evidence preview.
    cases = [c.case_id for c in service.list_cases()]
    if cases:
        st.markdown("#### Per-case governance compliance")
        selected = st.selectbox("Case", cases, key="gov_case_select")
        used, aset = service.evidence_for_consumption(selected)
        report = service.check_compliance(selected)
        c1, c2, c3 = st.columns(3)
        c1.metric("Mode", aset.mode.value)
        c2.metric("Included evidence", aset.included_count)
        c3.metric("Excluded evidence", aset.excluded_count)

        if report.is_compliant:
            st.success("Governance compliance: PASS")
        else:
            st.error(f"Governance compliance: {len(report.violations)} violation(s)")
            for v in report.violations:
                st.markdown(f"- **{v.code}** ({v.severity}): {v.description}")

        if aset.excluded:
            st.markdown("##### Excluded evidence")
            st.dataframe(
                [
                    {"fact": e.fact_type, "value": e.value, "reason": e.reason}
                    for e in aset.excluded
                ],
                use_container_width=True,
                hide_index=True,
            )


# --------------------------------------------------------------------------- #
# Tab: Quality Analytics (Milestone 11)
# --------------------------------------------------------------------------- #
def render_quality_analytics_tab() -> None:
    st.caption(
        "Organization-wide evidence-quality and workflow analytics, computed "
        "on demand from local storage."
    )

    service = get_case_service()
    a = service.quality_analytics()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cases", a.total_cases)
    c2.metric("Evidence", a.total_evidence)
    c3.metric("Decisions", a.evidence_decisions)
    c4.metric("Avg quality", f"{a.average_quality_score:.0%}")

    c5, c6, c7 = st.columns(3)
    c5.metric("Approval rate", f"{a.evidence_approval_rate:.0%}")
    c6.metric("Rejection rate", f"{a.evidence_rejection_rate:.0%}")
    c7.metric("Flag rate", f"{a.evidence_flag_rate:.0%}")

    c8, c9, c10 = st.columns(3)
    c8.metric("Weak evidence rate", f"{a.weak_evidence_rate:.0%}")
    c9.metric("Conflict rate", f"{a.conflict_rate:.0%}")
    c10.metric("Appeal success rate", f"{a.appeal_generation_success_rate:.0%}")

    st.metric("Avg review turnaround (s)", a.review_turnaround_seconds)

    st.markdown("#### Evidence decision mix")
    mix = {
        "approved": a.evidence_approval_rate,
        "rejected": a.evidence_rejection_rate,
        "flagged": a.evidence_flag_rate,
    }
    if a.evidence_decisions:
        st.bar_chart(mix)
    else:
        st.caption("No reviewer decisions recorded yet.")

    with st.expander("Raw analytics JSON"):
        st.json(a.as_dict())
