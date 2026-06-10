"""Streamlit dashboard for HealthAI.

Tabs:
- "Raw Text Extraction" (Milestone 1): upload a PDF/TXT and view raw text.
- "Structured Extraction" (Milestone 2): run the MedicalExtractionAgent and
  view structured JSON, a patient summary, and the confidence score.
- "Clinical Review" (Milestone 3): run the guideline review engine.
- "Appeal Generator" (Milestone 4): generate a prior-authorization appeal
  letter from the case + review + guideline.
- "Case Management" / "Human Review" / "Audit Log" / "Operational Metrics"
  (Milestone 5): track cases, record human decisions, view the audit trail,
  and see operational metrics. Backed by local SQLite.

Caching / LLM-call policy (Milestone: Architecture Hardening)
-------------------------------------------------------------
All derived data is cached in ``st.session_state`` keyed by a per-document
content signature (see ``app/ui/session.py``). Expensive LLM calls happen ONLY
when:
  1. a new document is uploaded (signature changes), or
  2. the user clicks an explicit "reprocess" / run button.

Because Streamlit reruns the whole script on every interaction (including tab
switches), the cache is what prevents redundant AI backend calls. Switching tabs
never triggers extraction or review. See ``docs/caching.md``.

AI access remains isolated behind the service layer; this UI only talks to the
agents, never to an SDK directly.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from app.agents.medical_extraction_agent import (
    ExtractionError,
    MedicalExtractionAgent,
)
from app.appeals.appeal_agent import AppealAgentError, AppealGenerationAgent
from app.extraction.extractor import extract_text_from_bytes
from app.extraction.size_validator import DocumentSizeValidator
from app.extraction.validation import ValidationError
from app.models.document import SUPPORTED_EXTENSIONS
from app.models.patient_case import Decision
from app.models.review_result import Recommendation
from app.review.comparison import compare_reviews
from app.review.review_agent import GuidelineReviewAgent
from app.services.factory import (
    describe_active_backend,
    get_llm_client,
)
from app.services.llm_client import LLMError
from app.services.local_client import LocalHeuristicClient
from app.services.provider_router import (
    AITask,
    describe_task_backend,
    get_client_for_task,
)
from app.ui import case_ui, session
from app.ui.tabs.common import get_case_service

# Directory holding generated mock healthcare documents.
SAMPLE_DOCS_DIR = Path(__file__).resolve().parents[2] / "data" / "sample_docs"

_SIZE_VALIDATOR = DocumentSizeValidator()


def get_patient_details_client():
    """Backward-compatible dashboard hook for structured extraction backend."""
    return get_client_for_task(AITask.STRUCTURED_EXTRACTION)


def describe_patient_details_backend() -> str:
    """Backward-compatible label for structured extraction backend."""
    return describe_task_backend(AITask.STRUCTURED_EXTRACTION)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _render_metadata(page_count: int, text: str) -> None:
    """Render document metadata as a row of metrics."""
    col1, col2, col3 = st.columns(3)
    col1.metric("Pages", page_count)
    col2.metric("Words", len(text.split()))
    col3.metric("Characters", len(text))


def _render_size_warnings(text: str, page_count: int) -> None:
    """Assess document size and surface warnings (detect-and-warn only)."""
    report = _SIZE_VALIDATOR.assess(text, page_count)
    cols = st.columns(3)
    cols[0].metric("Pages", report.page_count)
    cols[1].metric("Characters", f"{report.char_count:,}")
    cols[2].metric("Est. tokens", f"{report.estimated_tokens:,}")
    if report.exceeds_threshold:
        for w in report.warnings:
            st.warning(w)
        st.caption(
            "These are advisory warnings only. The document is not truncated "
            "or split; processing will still be attempted."
        )


def _render_sample_docs() -> None:
    """Show the bundled sample documents so users can try the app quickly."""
    if not SAMPLE_DOCS_DIR.is_dir():
        return

    samples = sorted(SAMPLE_DOCS_DIR.glob("*.txt")) + sorted(
        SAMPLE_DOCS_DIR.glob("*.pdf")
    )
    if not samples:
        return

    with st.sidebar:
        st.subheader("Sample documents")
        st.caption(
            "These mock healthcare files live in `data/sample_docs/`. "
            "Download one and upload it above to try the extractor."
        )
        for sample in samples:
            st.download_button(
                label=f"Download {sample.name}",
                data=sample.read_bytes(),
                file_name=sample.name,
                key=f"sample-{sample.name}",
            )


def _sync_uploaded_document(uploaded) -> bool:
    """Validate + extract an upload into session state (cached).

    Returns True if a document is ready in session state, False on error / no
    upload. Text extraction runs only when the document signature changes; on
    reruns (e.g. tab switches) the cached text is reused.

    A SINGLE shared uploader (rendered once in the sidebar) drives this. Using
    one uploader avoids the multi-widget problem where empty per-tab uploaders
    would clear the active document on every rerun.
    """
    if uploaded is None:
        session.clear_document()
        return False

    data = uploaded.getvalue()
    signature = session.document_signature(uploaded.name, data)
    is_new = session.set_active_document(signature, uploaded.name)

    if is_new or session.get_text() is None:
        try:
            document = extract_text_from_bytes(uploaded.name, data)
        except ValidationError as exc:
            st.error(f"Validation error: {exc}")
            session.clear_document()
            return False
        except Exception as exc:  # noqa: BLE001 - surface extraction failure
            st.error(f"Failed to extract text: {exc}")
            session.clear_document()
            return False
        session.set_text(document.text, document.page_count)

    return True


def _document_ready() -> bool:
    """True if a document has been uploaded and its text is cached."""
    return bool(session.get_text() is not None)


def _selected_case_record():
    """Return the selected CaseService record, if any."""
    case_id = session.get_persisted_case_id()
    if not case_id:
        return None
    return get_case_service().get_case(case_id)


def _selected_case_text() -> str:
    """Join stored document text for the selected case."""
    case_id = session.get_persisted_case_id()
    if not case_id:
        return ""
    docs = get_case_service().list_documents(case_id)
    return "\n\n".join(d.raw_text for d in docs if d.raw_text)


def _selected_case_stem() -> str:
    """Filename stem for downloads from upload or selected case."""
    filename = st.session_state.get(session.KEY_FILENAME)
    if filename:
        return Path(filename).stem
    case_id = session.get_persisted_case_id()
    return case_id or "case"


def _database_case_ready() -> bool:
    record = _selected_case_record()
    return bool(record and record.patient_case is not None)


def _active_pipeline_ready() -> bool:
    return _database_case_ready() or _document_ready()


def _get_or_extract_case(force: bool = False):
    """Return the cached PatientCase, running the agent only if needed.

    The MedicalExtractionAgent (potential AI backend call) runs only when there is
    no cached case or ``force`` is True (explicit reprocess).
    """
    record = _selected_case_record()
    if record and record.patient_case is not None and not force:
        session.set_case(record.patient_case)
        return record.patient_case

    cached = session.get_case()
    if cached is not None and not force:
        return cached

    text = session.get_text()
    if not text:
        return None

    with st.spinner("Running Medical Extraction Agent..."):
        try:
            agent = MedicalExtractionAgent(llm_client=get_patient_details_client())
            result = agent.extract(text)
        except ExtractionError as exc:
            st.error(f"Extraction failed: {exc}")
            return None

    session.set_case(
        result.case,
        session.ExtractionMeta(
            attempts=result.attempts,
            backend=result.backend,
            repaired=result.repaired,
        ),
    )
    return result.case


def _get_or_run_review(force: bool = False, mode: str = "auto"):
    """Return the cached review, running the review agent only if needed."""
    mode = mode.lower().strip()
    if mode not in {"auto", "ai", "gemini", "local", "compare"}:
        raise ValueError(f"Unknown review mode: {mode!r}")

    record = _selected_case_record()
    explicit_mode = mode in {"ai", "gemini", "local", "compare"}
    if record and record.review_result is not None and not force and not explicit_mode:
        used_ai = bool(getattr(record.review_result, "generated_by_ai", False))
        session.set_review(record.review_result, used_ai=used_ai)
        return record.review_result, used_ai

    cached = session.get_review()
    if cached is not None and not force and not explicit_mode:
        used_ai = session.get_review_used_ai() or bool(
            getattr(cached, "generated_by_ai", False)
        )
        return cached, used_ai

    case = _get_or_extract_case(force=False)
    text = _selected_case_text() or session.get_text()
    if case is None:
        return None, False

    if mode == "compare":
        try:
            gemini_client = get_llm_client(force="gemini")
        except LLMError as exc:
            st.error(
                "Gemini clinical reasoning is not configured. Set "
                "`GEMINI_API_KEY` or `GOOGLE_API_KEY`, then rerun compare mode. "
                f"Details: {exc}"
            )
            return None, False
        if not gemini_client.is_ai:
            st.error(
                "Gemini clinical reasoning is not available. Set `GEMINI_API_KEY` "
                "or `GOOGLE_API_KEY`, then rerun compare mode."
            )
            return None, False

        with st.spinner("Running local and Gemini clinical reviews..."):
            local_review = GuidelineReviewAgent(
                llm_client=LocalHeuristicClient()
            ).review(case, text)
            ai_review = GuidelineReviewAgent(llm_client=gemini_client).review(case, text)

        if not ai_review.used_ai:
            st.error(
                "Gemini did not produce a valid AI review, so compare mode did "
                "not replace the current review."
            )
            return None, False

        known_evidence_ids = None
        if record:
            known_evidence_ids = {
                ev.evidence_id for ev in get_case_service().list_evidence(record.case_id)
            }
        comparison = compare_reviews(
            local_review.result,
            ai_review.result,
            known_evidence_ids=known_evidence_ids,
        )
        ai_review.result.safety_gate = {
            **(ai_review.result.safety_gate or {}),
            "comparison": comparison.as_dict(),
        }
        session.set_review(ai_review.result, ai_review.used_ai)
        if record and record.patient_case is not None:
            get_case_service().attach_review(record.case_id, ai_review.result)
        return ai_review.result, ai_review.used_ai

    if mode == "local":
        llm_client = LocalHeuristicClient()
        spinner_label = "Running local rule-based clinical review..."
    elif mode == "gemini":
        try:
            llm_client = get_llm_client(force="gemini")
        except LLMError as exc:
            st.error(
                "Gemini clinical reasoning is not configured. Set "
                "`GEMINI_API_KEY` or `GOOGLE_API_KEY`, then rerun Gemini reasoning. "
                f"Details: {exc}"
            )
            return None, False
        if not llm_client.is_ai:
            st.error(
                "Gemini clinical reasoning is not available. Set `GEMINI_API_KEY` "
                "or `GOOGLE_API_KEY`, then rerun Gemini reasoning."
            )
            return None, False
        spinner_label = "Running Gemini clinical review..."
    else:
        llm_client = get_client_for_task(AITask.CLINICAL_REASONING)
        if mode == "ai" and not llm_client.is_ai:
            st.error(
                "AI reasoning is not configured. Set `ANTHROPIC_API_KEY`, "
                "`ANTHROPIC_AUTH_TOKEN`, `GEMINI_API_KEY`, or `GOOGLE_API_KEY`, "
                "then rerun AI reasoning."
            )
            return None, False
        spinner_label = (
            "Running AI clinical review..."
            if mode == "ai"
            else "Running clinical review..."
        )

    with st.spinner(spinner_label):
        agent = GuidelineReviewAgent(llm_client=llm_client)
        review = agent.review(case, text)

    session.set_review(review.result, review.used_ai)
    if record and record.patient_case is not None:
        get_case_service().attach_review(record.case_id, review.result)
    return review.result, review.used_ai


def _get_or_generate_appeal(force: bool = False):
    """Return the cached appeal, generating it only if needed.

    Depends on the cached case + review. The AppealGenerationAgent (potential
    AI backend call) runs only when there is no cached appeal or ``force`` is True.
    """
    record = _selected_case_record()
    if record and record.appeal_letter is not None and not force:
        session.set_appeal(record.appeal_letter, used_ai=False)
        return record.appeal_letter, False

    cached = session.get_appeal()
    if cached is not None and not force:
        return cached, session.get_appeal_used_ai()

    case = _get_or_extract_case(force=False)
    review, _ = _get_or_run_review(force=False)
    if case is None or review is None:
        return None, False

    with st.spinner("Generating appeal letter..."):
        agent = AppealGenerationAgent(
            llm_client=get_client_for_task(AITask.APPEAL_DRAFTING)
        )
        try:
            result = agent.generate(case, review)
        except AppealAgentError as exc:
            st.info(str(exc))
            return None, False

    session.set_appeal(result.appeal, result.used_ai)
    if record and record.patient_case is not None:
        get_case_service().attach_appeal(record.case_id, result.appeal)
    return result.appeal, result.used_ai


# --------------------------------------------------------------------------- #
# Tab 1: Raw text extraction (Milestone 1) - no LLM calls
# --------------------------------------------------------------------------- #
def _render_raw_text_tab() -> None:
    st.caption(
        "View the uploaded document's extracted raw text. No AI analysis on "
        "this tab."
    )

    if not _document_ready():
        st.info("Upload a PDF or TXT file in the sidebar to get started.")
        return

    text = session.get_text()
    page_count = session.get_page_count()

    st.success(f"Extracted text from **{st.session_state[session.KEY_FILENAME]}**.")
    _render_size_warnings(text, page_count)

    if not text.strip():
        st.warning(
            "No text could be extracted. The document may be empty or be a "
            "scanned image (OCR is not yet available)."
        )

    st.markdown("### Extracted text")
    st.text_area(
        label="Raw extracted text",
        value=text,
        height=500,
        label_visibility="collapsed",
        key="raw_text_area",
    )

    st.download_button(
        label="Download extracted text",
        data=text,
        file_name=f"{Path(st.session_state[session.KEY_FILENAME]).stem}_extracted.txt",
        mime="text/plain",
        key="raw_download",
    )


# --------------------------------------------------------------------------- #
# Tab 2: Structured extraction (Milestone 2)
# --------------------------------------------------------------------------- #
def _render_patient_summary(case) -> None:
    """Render a formatted patient summary from a PatientCase."""
    st.markdown("#### Patient summary")
    st.write(case.summary())

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Patient:** {case.patient_name or '—'}")
        st.markdown(f"**Member ID:** {case.member_id or '—'}")
        st.markdown(f"**Date of birth:** {case.date_of_birth or '—'}")
        st.markdown(f"**Insurance:** {case.insurance_company or '—'}")
        st.markdown(f"**Physician:** {case.physician_name or '—'}")
    with col2:
        st.markdown(f"**Diagnosis:** {case.diagnosis or '—'}")
        st.markdown(
            f"**ICD-10:** {', '.join(case.icd10_codes) if case.icd10_codes else '—'}"
        )
        st.markdown(f"**Requested service:** {case.requested_service or '—'}")
        st.markdown(
            f"**CPT:** {', '.join(case.cpt_codes) if case.cpt_codes else '—'}"
        )

    decision = case.decision
    if decision is Decision.DENIED:
        st.error("Decision: DENIED")
        st.markdown(f"**Denial reason:** {case.denial_reason or '—'}")
    elif decision is Decision.APPROVED:
        st.success("Decision: APPROVED")
    elif decision is Decision.PARTIAL:
        st.warning("Decision: PARTIAL")
    elif decision is Decision.PENDING:
        st.info("Decision: PENDING")
    else:
        st.info("Decision: UNKNOWN")


def _render_structured_tab() -> None:
    st.caption(
        "Upload a document, extract its text, and run the Medical Extraction "
        "Agent to produce a structured, validated patient record."
    )

    backend_desc = describe_patient_details_backend()
    st.info(f"Patient details backend: **{backend_desc}**")
    if "Local heuristic" in backend_desc:
        st.caption(
            "No hosted LLM API key detected, so the offline heuristic backend "
            "is in use. Set `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` and select "
            "the matching backend to use a hosted model."
        )
    elif "Gemini" in backend_desc:
        st.caption(
            "Structured patient details are produced with Gemini when available, "
            "so downstream review and appeal tabs use the cleaner normalized case."
        )

    if not _active_pipeline_ready():
        st.info("Upload a denial or approval letter in the sidebar to extract structured data.")
        return

    text = _selected_case_text() or session.get_text()
    if text is not None and not text.strip() and not _database_case_ready():
        st.warning("No text could be extracted; cannot run structured extraction.")
        return

    if text:
        _render_size_warnings(text, session.get_page_count())

    cached_case = _get_or_extract_case(force=False) if _database_case_ready() else session.get_case()
    # Buttons: first run vs. explicit reprocess. Tab switches hit neither.
    col_run, col_reprocess = st.columns(2)
    run = col_run.button(
        "Run structured extraction",
        type="primary",
        key="run_extract",
        disabled=cached_case is not None,
        help="Runs the extraction agent (may call the configured AI backend).",
    )
    reprocess = col_reprocess.button(
        "Reprocess",
        key="reprocess_extract",
        disabled=cached_case is None,
        help="Force a fresh extraction (re-runs the configured AI backend).",
    )

    if reprocess:
        session.invalidate_case_and_review()

    if cached_case is None and not run:
        with st.expander("Preview extracted text"):
            st.text(text[:2000])
        st.info("Click **Run structured extraction** to analyze this document.")
        return

    case = _get_or_extract_case(force=reprocess)
    if case is None:
        return

    if session.get_case() is not None and not run and not reprocess:
        st.caption("Showing cached result (no new AI backend call was made).")

    meta = session.get_extraction_meta()

    conf_col, attempts_col, backend_col = st.columns(3)
    conf_col.metric("Confidence", f"{case.confidence_score:.0%}")
    if meta:
        attempts_col.metric("Attempts", meta.attempts)
        backend_col.metric("Backend", meta.backend)
    st.progress(case.confidence_score)
    gate = getattr(case, "safety_gate", {}) or {}
    if gate.get("status") == "HUMAN_REVIEW_REQUIRED":
        st.warning("Safety gate: human review required.")
        for reason in gate.get("reasons", []):
            st.markdown(f"- {reason}")

    if meta and meta.repaired:
        st.caption(
            f"Note: the model self-corrected after {meta.attempts - 1} "
            "invalid response(s) before producing valid JSON."
        )

    _render_patient_summary(case)

    st.markdown("#### Structured JSON")
    st.json(case.model_dump(mode="json"))

    st.download_button(
        label="Download structured JSON",
        data=case.model_dump_json(indent=2),
        file_name=f"{_selected_case_stem()}_structured.json",
        mime="application/json",
        key="structured_download",
    )


# --------------------------------------------------------------------------- #
# Tab 3: Clinical review (Milestone 3)
# --------------------------------------------------------------------------- #
def _render_recommendation_banner(rec: Recommendation) -> None:
    if rec is Recommendation.APPROVE:
        st.success("Recommendation: APPROVE")
    elif rec is Recommendation.DENY:
        st.error("Recommendation: DENY")
    else:
        st.warning("Recommendation: INSUFFICIENT INFORMATION")


def _render_clinical_review_tab() -> None:
    st.caption(
        "Upload a document, extract the patient case, and run the clinical "
        "review engine to check the request against medical-necessity "
        "guidelines."
    )

    backend_desc = describe_active_backend()
    st.info(f"Active backend: **{backend_desc}**")
    st.caption(
        "Review uses the configured AI-backed agent when an API key is configured, "
        "and a deterministic rule-based engine otherwise. The output schema is "
        "identical either way."
    )
    st.caption(
        f"Patient details source: **{describe_patient_details_backend()}**. "
        "Those structured details feed both AI reasoning and local rule review."
    )

    if not _active_pipeline_ready():
        st.info("Upload a prior-authorization letter in the sidebar to run a clinical review.")
        return

    text = _selected_case_text() or session.get_text()
    if text is not None and not text.strip() and not _database_case_ready():
        st.warning("No text could be extracted; cannot run a clinical review.")
        return

    if text:
        _render_size_warnings(text, session.get_page_count())

    record = _selected_case_record()
    cached_review = (
        record.review_result
        if record and record.review_result is not None
        else session.get_review()
    )
    col_ai, col_local, col_compare, col_clear = st.columns(4)
    run_ai = col_ai.button(
        "Gemini reasoning",
        type="primary",
        key="run_review_ai",
        help=(
            "Extracts the case if needed, then runs clinical review using the "
            "Gemini hosted AI backend."
        ),
    )
    run_local = col_local.button(
        "Local rule review",
        key="run_review_local",
        help=(
            "Extracts the case if needed, then runs the deterministic local "
            "rule engine with no AI backend call."
        ),
    )
    run_compare = col_compare.button(
        "Compare review",
        key="run_review_compare",
        help=(
            "Runs both deterministic local review and Gemini review, then logs "
            "material disagreements for safety."
        ),
    )
    clear_review = col_clear.button(
        "Clear review",
        key="clear_review",
        disabled=cached_review is None,
        help="Clears the cached review so no previous result is shown.",
    )

    if run_ai or run_local or run_compare or clear_review:
        session.invalidate_review()

    if (
        cached_review is None
        and not run_ai
        and not run_local
        and not run_compare
        and not _database_case_ready()
    ):
        with st.expander("Preview extracted text"):
            st.text(text[:2000])
        st.info(
            "Choose **Gemini reasoning**, **Local rule review**, or "
            "**Compare review** to analyze this document."
        )
        return

    case = _get_or_extract_case(force=False)
    if case is None:
        return

    if clear_review and not run_ai and not run_local and not run_compare:
        st.info(
            "Review cleared. Choose **Gemini reasoning**, **Local rule review**, "
            "or **Compare review** to run it again."
        )
        return

    review_mode = (
        "compare" if run_compare else "gemini" if run_ai else "local" if run_local else "auto"
    )
    result, used_ai = _get_or_run_review(
        force=(run_ai or run_local or run_compare),
        mode=review_mode,
    )
    if result is None:
        return

    if cached_review is not None and not run_ai and not run_local and not run_compare:
        st.caption("Showing cached review (no new AI backend call was made).")

    _render_recommendation_banner(result.recommendation)

    conf_col, guideline_col, ai_col = st.columns(3)
    conf_col.metric("Confidence", f"{result.confidence_score:.0%}")
    guideline_col.metric("Guideline", result.guideline_id or "none matched")
    ai_col.metric("Reasoning", "AI backend" if used_ai else "Rule engine")
    st.progress(result.confidence_score)
    gate = getattr(result, "safety_gate", {}) or {}
    if gate.get("status") == "HUMAN_REVIEW_REQUIRED":
        st.warning("Safety gate: human review required.")
        for reason in gate.get("reasons", []):
            st.markdown(f"- {reason}")
    comparison = gate.get("comparison") or {}
    if comparison:
        with st.expander("Compare mode results"):
            st.markdown(
                f"**Local:** {comparison.get('deterministic_recommendation', 'unknown')}  "
                f"**Gemini:** {comparison.get('ai_recommendation', 'unknown')}"
            )
            material = comparison.get("material_disagreements") or []
            non_material = comparison.get("non_material_differences") or []
            if material:
                st.warning("Material disagreement logged for human review.")
                for item in material:
                    st.markdown(f"- {item}")
            elif non_material:
                st.info("Only non-material differences were found.")
                for item in non_material:
                    st.markdown(f"- {item}")
            else:
                st.success("No meaningful disagreement found.")

    with st.expander("Patient case used for review"):
        _render_patient_summary(case)

    col_match, col_missing = st.columns(2)
    with col_match:
        st.markdown("#### ✅ Matched criteria")
        if result.matched_criteria:
            for c in result.matched_criteria:
                st.markdown(f"- {c}")
        else:
            st.caption("None")
    with col_missing:
        st.markdown("#### ❌ Missing criteria")
        if result.missing_criteria:
            for c in result.missing_criteria:
                st.markdown(f"- {c}")
        else:
            st.caption("None")

    if result.contraindications_found:
        st.markdown("#### ⚠️ Contraindications")
        for c in result.contraindications_found:
            st.markdown(f"- {c}")

    if result.missing_evidence:
        st.markdown("#### 🔍 Missing evidence")
        for e in result.missing_evidence:
            st.markdown(f"- {e}")

    if result.recommended_actions:
        st.markdown("#### 🧭 Recommended actions")
        for a in result.recommended_actions:
            st.markdown(f"- {a}")

    st.markdown("#### Rationale")
    st.write(result.rationale)

    st.markdown("#### Structured JSON")
    st.json(result.model_dump(mode="json"))

    st.download_button(
        label="Download review JSON",
        data=result.model_dump_json(indent=2),
        file_name=f"{_selected_case_stem()}_review.json",
        mime="application/json",
        key="review_download",
    )


# --------------------------------------------------------------------------- #
# Tab 4: Appeal generator (Milestone 4)
# --------------------------------------------------------------------------- #
def _render_appeal_tab() -> None:
    st.caption(
        "Generate a professional prior-authorization appeal letter from the "
        "extracted case, the clinical review, and the matched guideline."
    )

    backend_desc = describe_active_backend()
    st.info(f"Active backend: **{backend_desc}**")
    st.caption(
        "Appeal drafting uses the configured AI-backed agent when an API key is "
        "configured, and a deterministic letter builder otherwise. Both honor "
        "the same safety rules: no fabricated clinical facts."
    )
    st.caption(
        f"Patient details source: **{describe_patient_details_backend()}**. "
        "The appeal uses these structured details when drafting patient and case sections."
    )

    if not _active_pipeline_ready():
        st.info("Upload a denial letter in the sidebar to generate an appeal.")
        return

    text = _selected_case_text() or session.get_text()
    if text is not None and not text.strip() and not _database_case_ready():
        st.warning("No text could be extracted; cannot generate an appeal.")
        return

    if text:
        _render_size_warnings(text, session.get_page_count())

    record = _selected_case_record()
    cached_appeal = record.appeal_letter if record and record.appeal_letter is not None else session.get_appeal()
    col_run, col_reprocess = st.columns(2)
    run = col_run.button(
        "Generate appeal",
        type="primary",
        key="run_appeal",
        disabled=cached_appeal is not None,
        help="Extracts the case + review (if needed) and drafts the appeal "
        "(may call the configured AI backend).",
    )
    reprocess = col_reprocess.button(
        "Regenerate",
        key="reprocess_appeal",
        disabled=cached_appeal is None,
        help="Force a fresh appeal letter (re-runs the configured AI backend).",
    )

    if reprocess:
        session.invalidate_appeal()

    case_for_gate = (
        record.patient_case
        if record and record.patient_case is not None
        else session.get_case()
    )
    if cached_appeal is None and not run and not reprocess:
        if case_for_gate is not None and case_for_gate.decision is not Decision.DENIED:
            st.info("Appeal generation requires an active denied case.")
        else:
            st.info("Click **Generate appeal** to draft a letter for this document.")
        return

    # Ensure prerequisites (case + review) exist; these are cached and only
    # invoke the configured AI backend if not already computed.
    case = _get_or_extract_case(force=False)
    if case is None:
        return
    if cached_appeal is None and case.decision is not Decision.DENIED:
        st.info("Appeal generation requires an active denied case.")
        return
    review, _ = _get_or_run_review(force=False)
    if review is None:
        return

    appeal, used_ai = _get_or_generate_appeal(force=reprocess)
    if appeal is None:
        return

    if cached_appeal is not None and not run and not reprocess:
        st.caption("Showing cached appeal (no new AI backend call was made).")

    # Appeal summary + confidence.
    conf_col, decision_col, ai_col = st.columns(3)
    conf_col.metric("Confidence", f"{appeal.confidence_score:.0%}")
    decision_col.metric("Original decision", (appeal.original_decision or "—").upper())
    ai_col.metric("Drafted by", "AI backend" if used_ai else "Letter builder")
    st.progress(appeal.confidence_score)
    if appeal.verification.status.value != "NOT_RUN":
        st.caption(f"Appeal verification: {appeal.verification.status.value}")
    gate = getattr(appeal, "safety_gate", {}) or {}
    if gate.get("status") == "HUMAN_REVIEW_REQUIRED":
        st.warning("Safety gate: human review required.")
        for reason in gate.get("reasons", []):
            st.markdown(f"- {reason}")

    st.markdown("#### Appeal summary")
    st.write(appeal.summary())
    st.markdown(f"**Appeal ID:** {appeal.appeal_id}")
    if appeal.appeal_reason:
        st.markdown("**Reason for appeal**")
        st.write(appeal.appeal_reason)

    if appeal.guideline_support:
        st.markdown("#### 📚 Guideline support")
        for item in appeal.guideline_support:
            st.markdown(f"- {item}")

    if appeal.missing_information:
        st.markdown("#### 🔍 Missing information")
        st.caption(
            "These items were not available in the record and are surfaced "
            "honestly (not asserted as fact)."
        )
        for item in appeal.missing_information:
            st.markdown(f"- {item}")

    if appeal.recommended_next_steps:
        st.markdown("#### 🧭 Recommended next steps")
        for item in appeal.recommended_next_steps:
            st.markdown(f"- {item}")

    st.markdown("#### Generated letter")
    st.markdown(appeal.letter_text)

    stem = _selected_case_stem()
    col_txt, col_md = st.columns(2)
    col_txt.download_button(
        label="Download as TXT",
        data=appeal.to_txt(),
        file_name=f"{stem}_appeal.txt",
        mime="text/plain",
        key="appeal_download_txt",
    )
    col_md.download_button(
        label="Download as Markdown",
        data=appeal.to_markdown(),
        file_name=f"{stem}_appeal.md",
        mime="text/markdown",
        key="appeal_download_md",
    )

    with st.expander("Structured appeal JSON"):
        st.json(appeal.model_dump(mode="json"))

    # Persist the full case (extraction + review + appeal) so it enters the
    # case-management workflow and the human-review queue.
    case_id = case_ui.persist_current_case()
    if case_id:
        st.caption(
            f"Saved to case **{case_id}** — now pending human review. See the "
            "Case Management and Human Review tabs."
        )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def render_dashboard() -> None:
    """Render the full Streamlit dashboard."""
    st.set_page_config(
        page_title="HealthAI - Prior Authorization",
        page_icon="🏥",
        layout="wide",
    )

    session.init_state()

    st.title("🏥 HealthAI")
    st.subheader("Prior Authorization Document Intelligence")

    # Single shared uploader (one source of truth) rendered once in the
    # sidebar. Synced a single time per rerun so tab bodies never re-sync or
    # clear each other. Text extraction is cached by document signature.
    with st.sidebar:
        st.subheader("Document")
        uploaded = st.file_uploader(
            "Upload a document",
            type=sorted(SUPPORTED_EXTENSIONS.keys()),
            accept_multiple_files=False,
            help="Supported formats: PDF and TXT. Shared across all tabs.",
            key="shared_uploader",
        )
    _sync_uploaded_document(uploaded)

    _render_sample_docs()

    (
        raw_tab,
        structured_tab,
        review_tab,
        appeal_tab,
        ingestion_tab,
        ocr_tab,
        assembly_tab,
        evidence_tab,
        quality_tab,
        workbench_tab,
        conflict_tab,
        resolution_tab,
        feedback_tab,
        cases_tab,
        human_tab,
        audit_tab,
        metrics_tab,
        governance_tab,
        analytics_tab,
        review_explain_tab,
        appeal_explain_tab,
        payer_tab,
        ops_health_tab,
        validation_tab,
    ) = st.tabs(
        [
            "Raw Text Extraction",
            "Structured Extraction",
            "Clinical Review",
            "Appeal Generator",
            "Document Ingestion",
            "OCR Status",
            "Document Assembly",
            "Evidence Explorer",
            "Evidence Quality",
            "Reviewer Workbench",
            "Conflict Review",
            "Conflict Resolution",
            "Reviewer Feedback",
            "Case Management",
            "Human Review",
            "Audit Log",
            "Operational Metrics",
            "Governance Settings",
            "Quality Analytics",
            "Review Explainability",
            "Appeal Explainability",
            "Payer Management",
            "Operational Health",
            "Validation Runner",
        ]
    )
    with raw_tab:
        _render_raw_text_tab()
    with structured_tab:
        _render_structured_tab()
    with review_tab:
        _render_clinical_review_tab()
    with appeal_tab:
        _render_appeal_tab()
    with ingestion_tab:
        case_ui.render_document_ingestion_tab()
    with ocr_tab:
        case_ui.render_ocr_explorer_tab()
    with assembly_tab:
        case_ui.render_document_assembly_tab()
    with evidence_tab:
        case_ui.render_evidence_explorer_tab()
    with quality_tab:
        case_ui.render_evidence_quality_tab()
    with workbench_tab:
        case_ui.render_reviewer_workbench_tab()
    with conflict_tab:
        case_ui.render_conflict_review_tab()
    with resolution_tab:
        case_ui.render_conflict_resolution_tab()
    with feedback_tab:
        case_ui.render_reviewer_feedback_tab()
    with cases_tab:
        case_ui.render_case_management_tab()
    with human_tab:
        case_ui.render_human_review_tab()
    with audit_tab:
        case_ui.render_audit_log_tab()
    with metrics_tab:
        case_ui.render_metrics_tab()
    with governance_tab:
        case_ui.render_governance_settings_tab()
    with analytics_tab:
        case_ui.render_quality_analytics_tab()
    with review_explain_tab:
        case_ui.render_review_explainability_tab()
    with appeal_explain_tab:
        case_ui.render_appeal_explainability_tab()
    with payer_tab:
        case_ui.render_payer_management_tab()
    with ops_health_tab:
        case_ui.render_operational_health_tab()
    with validation_tab:
        case_ui.render_validation_runner_tab()
