"""Assembly tabs: Document Assembly, Evidence Explorer, Conflict Review (M6/7).

Moved verbatim from the old ``case_ui.py`` during the Milestone 12 UI split.
"""

from __future__ import annotations

import streamlit as st

from app.extraction.extractor import extract_text_from_bytes
from app.models.case_document import DocumentCategory
from app.models.conflict_report import ConflictSeverity
from app.ui import session
from app.ui.tabs.common import get_case_service, select_or_create_case


# --------------------------------------------------------------------------- #
# Tab: Document Assembly (Milestone 6/7)
# --------------------------------------------------------------------------- #
def render_document_assembly_tab() -> None:
    st.caption(
        "Attach multiple supporting documents to a case (denial letter, "
        "clinical notes, labs, imaging, ...) and assemble a unified, "
        "evidence-backed view."
    )

    service = get_case_service()
    case_id = select_or_create_case(service, key_prefix="assembly")
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
            record = service.get_case(case_id)
            patient_case = (
                record.patient_case
                if record and record.patient_case is not None
                else ctx.patient_case
            )
            session.refresh_assembled_case(case_id, patient_case)
            st.rerun()


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
