"""Ingestion tabs: Document Ingestion (M9) and OCR Explorer (M9).

Moved verbatim from the old ``case_ui.py`` during the Milestone 12 UI split.
"""

from __future__ import annotations

import streamlit as st

from app.models.case_document import DocumentCategory
from app.models.ocr_result import DEFAULT_OCR_CONFIDENCE_THRESHOLD
from app.ui.tabs.common import get_case_service, select_or_create_case


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

    case_id = select_or_create_case(service, key_prefix="ingestion")
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
