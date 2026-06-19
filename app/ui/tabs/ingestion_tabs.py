"""Ingestion tabs: document ingestion and OCR status."""

from __future__ import annotations

import streamlit as st

from app.models.case_document import DocumentCategory
from app.models.ocr_result import DEFAULT_OCR_CONFIDENCE_THRESHOLD
from app.ui import session
from app.ui.tabs.common import get_case_service, select_or_create_case


_INGEST_TYPES = ["txt", "pdf", "png", "jpg", "jpeg"]
_TEXT_OR_PDF_TYPES = ["txt", "pdf"]


def render_document_ingestion_tab() -> None:
    st.caption(
        "Upload TXT files, PDFs, or scanned/image documents. HealthAI detects "
        "whether a text layer exists and runs OCR only when a supported OCR "
        "provider is available."
    )

    service = get_case_service()
    readiness = service.ocr_readiness()
    st.info(f"Active OCR provider: **{readiness.description}**")
    if not readiness.is_available:
        st.warning(readiness.message)
        st.caption(
            "TXT files and searchable PDFs can still be ingested. Scanned PDFs "
            "and image uploads require Tesseract OCR."
        )
    elif not readiness.is_real_ocr:
        st.caption(
            readiness.message + " Install Tesseract and its Python imaging "
            "dependencies for real image OCR."
        )

    case_id = select_or_create_case(service, key_prefix="ingestion")
    if not case_id:
        st.info("Create or select a case to ingest documents.")
        return

    threshold = st.slider(
        "OCR confidence threshold (low-confidence pages are flagged)",
        min_value=0.0,
        max_value=1.0,
        value=float(DEFAULT_OCR_CONFIDENCE_THRESHOLD),
        step=0.05,
        key="ingest_threshold",
    )

    uploads = st.file_uploader(
        "Upload documents (TXT, PDF, PNG, JPG, JPEG)",
        type=_INGEST_TYPES if readiness.is_available else _TEXT_OR_PDF_TYPES,
        accept_multiple_files=True,
        key=session.widget_key("ingest_uploader"),
    )
    type_options = ["(auto-detect)"] + [c.value for c in DocumentCategory]
    override = st.selectbox(
        "Document type",
        type_options,
        key=session.widget_key("ingest_doc_type"),
    )

    if uploads and st.button("Ingest document(s)", type="primary", key="ingest_run"):
        for up in uploads:
            override_val = None if override == "(auto-detect)" else override
            doc, result = service.ingest_document(
                case_id,
                up.name,
                up.getvalue(),
                category_override=override_val,
                ocr_confidence_threshold=threshold,
            )
            method = (
                result.ocr_results[0].processing_method.value
                if result.ocr_results
                else ("TEXT" if result.kind.value == "TEXT" else "TEXT_LAYER")
            )
            msg = (
                f"Ingested **{up.name}** as {result.kind.value} "
                f"(type={doc.document_type.value}, pages={result.page_count}, "
                f"method={method})."
            )
            if not result.ocr_available:
                st.warning(msg + " OCR unavailable; no text extracted.")
            elif result.ocr_used and result.low_confidence_pages(threshold):
                st.warning(
                    msg + f" Low-confidence pages: {result.low_confidence_pages(threshold)}. "
                    "Inspect source text in OCR Status."
                )
            else:
                st.success(msg)
            for warning in result.warnings:
                st.warning(warning)
        st.rerun()

    docs = service.list_documents(case_id)
    if docs:
        st.markdown(f"#### Documents in `{case_id}`")
        statuses = {s.document_id: s for s in service.document_ocr_statuses(case_id)}
        st.dataframe(
            [
                {
                    "filename": d.filename,
                    "type": d.document_type.value,
                    "pages": d.page_count,
                    "ocr_status": statuses[d.document_id].status,
                    "ocr_detail": statuses[d.document_id].detail,
                    "ocr_pages": statuses[d.document_id].ocr_pages,
                }
                for d in docs
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.caption("Assemble the case in the Document Assembly tab to build evidence.")


def render_ocr_explorer_tab() -> None:
    st.caption(
        "Inspect OCR status, page-by-page OCR output, confidence, processing "
        "method, and classified document type."
    )

    service = get_case_service()
    cases_with_docs = [
        c.case_id for c in service.list_cases()
        if service.list_documents(c.case_id)
    ]
    if not cases_with_docs:
        st.info("No ingested documents yet.")
        return

    selected = st.selectbox("Case", cases_with_docs, key="ocr_case_select")
    threshold = st.slider(
        "Low-confidence threshold",
        0.0,
        1.0,
        float(DEFAULT_OCR_CONFIDENCE_THRESHOLD),
        0.05,
        key="ocr_threshold",
    )

    docs = {d.document_id: d for d in service.list_documents(selected)}
    statuses = service.document_ocr_statuses(selected)
    st.dataframe(
        [
            {
                "filename": status.filename,
                "ocr_status": status.status,
                "detail": status.detail,
                "method": status.processing_method,
                "ocr_pages": status.ocr_pages,
            }
            for status in statuses
        ],
        use_container_width=True,
        hide_index=True,
    )

    results = service.list_ocr_results(selected)
    if not results:
        st.info(
            "No persisted OCR page output exists for this case. See the "
            "document status table above."
        )
        return

    by_doc: dict[str, list] = {}
    for result in results:
        by_doc.setdefault(result.document_id, []).append(result)

    for document_id, pages in by_doc.items():
        doc = docs.get(document_id)
        title = doc.filename if doc else document_id
        dtype = doc.document_type.value if doc else "?"
        st.markdown(f"#### {title} - classified: **{dtype}**")
        for page in sorted(pages, key=lambda p: p.page_number):
            low = page.confidence < threshold
            label = (
                f"Page {page.page_number} - {page.processing_method.value} - "
                f"confidence {page.confidence:.0%}"
            )
            if low:
                st.error(label + " LOW CONFIDENCE")
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
