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

import html
import shutil
import textwrap
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
from app.models.case_record import CaseStatus
from app.models.case_document import DocumentCategory
from app.models.document import SUPPORTED_EXTENSIONS
from app.models.ocr_result import DEFAULT_OCR_CONFIDENCE_THRESHOLD
from app.models.patient_case import Decision
from app.models.review_result import CriterionStatus, Recommendation
from app.review.comparison import compare_reviews, reconcile_ai_review_with_deterministic
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
from app.ui.tabs.common import (
    get_case_service,
    reset_case_service,
    select_or_create_case,
)

# Directory holding generated mock healthcare documents.
SAMPLE_DOCS_DIR = Path(__file__).resolve().parents[2] / "data" / "sample_docs"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HEALTHAI_DB_PATH = PROJECT_ROOT / "data" / "healthai.db"

_SIZE_VALIDATOR = DocumentSizeValidator()
_RESET_MESSAGE_KEY = "workspace_reset_message"
_RESET_ERROR_KEY = "workspace_reset_errors"


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


def _relative_display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _clear_streamlit_runtime_caches() -> list[str]:
    """Clear Streamlit caches when available, returning non-fatal errors."""
    errors: list[str] = []
    for cache_name in ("cache_data", "cache_resource"):
        cache_api = getattr(st, cache_name, None)
        clear = getattr(cache_api, "clear", None)
        if clear is None:
            continue
        try:
            clear()
        except Exception as exc:  # noqa: BLE001 - reset should keep going
            errors.append(f"{cache_name}.clear(): {exc}")
    return errors


def _delete_reset_artifacts(
    *,
    project_root: Path = PROJECT_ROOT,
    db_path: Path = DEFAULT_HEALTHAI_DB_PATH,
) -> tuple[list[str], list[str]]:
    """Remove local app artifacts matching the manual reset commands."""
    targets = [
        project_root / ".streamlit" / "cache",
        project_root / ".pytest_cache",
        db_path,
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
    ]
    removed: list[str] = []
    errors: list[str] = []

    for target in targets:
        try:
            if target.is_dir():
                shutil.rmtree(target)
                removed.append(_relative_display_path(target))
            elif target.exists():
                target.unlink()
                removed.append(_relative_display_path(target))
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001 - mimic SilentlyContinue
            errors.append(f"{_relative_display_path(target)}: {exc}")

    return removed, errors


def _reset_structured_workspace() -> None:
    """Reset session, caches, local DB, and upload widget generation."""
    reset_case_service()
    cache_errors = _clear_streamlit_runtime_caches()
    session.reset_workspace_state()
    _removed, delete_errors = _delete_reset_artifacts()

    st.session_state[_RESET_MESSAGE_KEY] = (
        "Workspace reset. Current document, selected case, cached extraction, "
        "reviews, appeals, Streamlit cache, pytest cache, and local case database "
        "were cleared."
    )
    st.session_state[_RESET_ERROR_KEY] = cache_errors + delete_errors
    st.rerun()


def _render_reset_notice() -> None:
    message = st.session_state.pop(_RESET_MESSAGE_KEY, None)
    errors = st.session_state.pop(_RESET_ERROR_KEY, [])
    if message:
        st.success(message)
    for error in errors:
        st.warning(f"Reset warning: {error}")


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
    return _combined_document_text(docs)


def _combined_document_text(docs) -> str:
    """Join all document text with filename context for extraction prompts."""
    chunks = []
    for doc in docs:
        raw_text = (doc.raw_text or "").strip()
        if not raw_text:
            continue
        chunks.append(
            "\n".join(
                [
                    f"Document: {doc.filename}",
                    f"Document type: {doc.document_type.value}",
                    "",
                    raw_text,
                ]
            )
        )
    return "\n\n".join(chunks)


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


def _get_or_extract_case(force: bool = False, mode: str = "auto"):
    """Return the cached PatientCase, running the agent only if needed.

    The MedicalExtractionAgent (potential AI backend call) runs only when there is
    no cached case or ``force`` is True (explicit reprocess).
    """
    mode = mode.lower().strip()
    if mode not in {"auto", "ai", "local"}:
        raise ValueError(f"Unknown extraction mode: {mode!r}")
    record = _selected_case_record()
    if record and record.patient_case is not None and not force:
        session.set_case(record.patient_case)
        return record.patient_case

    cached = session.get_case()
    if cached is not None and not force:
        return cached

    text = _selected_case_text() or session.get_text()
    if not text:
        return None

    if mode == "local":
        llm_client = LocalHeuristicClient()
        spinner_label = "Running local structured extraction..."
    elif mode == "ai":
        llm_client = get_client_for_task(AITask.STRUCTURED_EXTRACTION)
        if not llm_client.is_ai:
            st.error(
                "AI structured extraction is not configured. Set an AI backend "
                "API key, then rerun AI extraction."
            )
            return None
        spinner_label = "Running AI structured extraction..."
    else:
        llm_client = get_patient_details_client()
        spinner_label = "Running Medical Extraction Agent..."

    with st.spinner(spinner_label):
        try:
            agent = MedicalExtractionAgent(llm_client=llm_client)
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
    if record:
        get_case_service().attach_extraction(record.case_id, result.case)
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
        reconcile_ai_review_with_deterministic(local_review.result, ai_review.result)
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

    appeal = result.appeal
    if record and record.patient_case is not None:
        updated = get_case_service().attach_appeal(record.case_id, appeal)
        if updated.appeal_letter is not None:
            appeal = updated.appeal_letter
    session.set_appeal(appeal, result.used_ai)
    return appeal, result.used_ai


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
    reset_col, _ = st.columns([1, 5])
    if reset_col.button(
        "Reset",
        key=session.widget_key("structured_workspace_reset"),
        help=(
            "Clears the current document, selected case, cached results, local "
            "Streamlit cache, pytest cache, and data/healthai.db."
        ),
    ):
        _reset_structured_workspace()
    _render_reset_notice()

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
    col_local, col_ai, col_reprocess = st.columns(3)
    run_local = col_local.button(
        "Run local extraction",
        type="primary",
        key="run_extract_local",
        disabled=cached_case is not None,
        help="Runs the offline deterministic extraction backend.",
    )
    run_ai = col_ai.button(
        "Run AI extraction",
        key="run_extract_ai",
        disabled=cached_case is not None,
        help="Runs the configured hosted AI backend for structured extraction.",
    )
    reprocess = col_reprocess.button(
        "Reprocess",
        key="reprocess_extract",
        disabled=cached_case is None,
        help="Force a fresh extraction using the configured default backend.",
    )

    if reprocess:
        session.invalidate_case_and_review()

    if cached_case is None and not (run_local or run_ai):
        with st.expander("Preview extracted text"):
            st.text(text[:2000])
        st.info("Click **Run local extraction** or **Run AI extraction** to analyze this document.")
        return

    extract_mode = "local" if run_local else "ai" if run_ai else "auto"
    case = _get_or_extract_case(force=reprocess or run_local or run_ai, mode=extract_mode)
    if case is None:
        return

    if session.get_case() is not None and not run_local and not run_ai and not reprocess:
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

    _render_appeal_workspace(record, case, review, appeal, used_ai)
    _persist_appeal_case()
    return

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


def _appeal_case_summary(case, record, appeal) -> list[tuple[str, str]]:
    denial_date = getattr(record, "created_at", None)
    if not denial_date and appeal:
        denial_date = appeal.created_at[:10]
    return [
        ("Diagnosis", case.diagnosis if case and case.diagnosis else "Unavailable"),
        (
            "Requested Service",
            case.requested_service if case and case.requested_service else "Unavailable",
        ),
        ("Date of Denial", denial_date or "Unavailable"),
        (
            "Denial Reason (from payer)",
            case.denial_reason if case and case.denial_reason else "Documentation not available",
        ),
    ]


def _appeal_review_lines(review) -> list[tuple[str, str]]:
    recommendation = (
        review.recommendation.value.replace("_", " ") if review else "Unknown"
    )
    return [
        ("Matched Criteria", str(len(review.matched_criteria)) if review else "0"),
        ("Missing Criteria", str(len(review.missing_criteria)) if review else "0"),
        ("Confidence Score", f"{_percent(review.confidence_score)}%" if review else "0%"),
        ("Recommendation", recommendation),
    ]


def _appeal_strategy_lines(appeal) -> list[str]:
    lines = []
    if appeal and appeal.appeal_reason:
        lines.append(appeal.appeal_reason)
    if appeal and appeal.recommended_next_steps:
        lines.extend(appeal.recommended_next_steps[:3])
    if not lines:
        lines.append("Address missing documentation and emphasize medical necessity.")
    return lines


def _appeal_evidence_rows(appeal, evidence) -> list[tuple[str, str, str, str]]:
    selected_ids = set(appeal.evidence_ids if appeal else [])
    if appeal:
        for ids in (appeal.section_evidence or {}).values():
            selected_ids.update(ids)
    by_id = {item.evidence_id: item for item in evidence}
    ordered = [by_id[eid] for eid in selected_ids if eid in by_id]
    if not ordered:
        ordered = evidence[:4]

    rows = []
    for item in ordered[:4]:
        quality = (
            "High"
            if item.confidence_score >= 0.95
            else "Medium"
            if item.confidence_score >= 0.70
            else "Low"
        )
        rows.append(
            (
                item.section_label or item.field_name or item.source_filename or "Supporting Evidence",
                _short(item.quoted_text or item.normalized_fact, 72),
                item.citation(),
                quality,
            )
        )
    if not rows:
        rows.append(
            (
                "Generated appeal basis",
                appeal.appeal_reason if appeal and appeal.appeal_reason else "Clinical record and review results",
                "Case record",
                "High",
            )
        )
    return rows


def _appeal_citations(appeal) -> list[str]:
    if appeal and appeal.citations:
        return appeal.citations[:4]
    if appeal and appeal.evidence_ids:
        return appeal.evidence_ids[:4]
    return ["Evidence citations will appear here once linked evidence is available."]


def _render_appeal_card(title: str, lines) -> None:
    body = []
    if lines and isinstance(lines[0], tuple):
        for label, value in lines:
            value_html = (
                f'<span class="ha-status-chip">{_safe(value)}</span>'
                if label == "Recommendation"
                else _safe(value)
            )
            body.append(
                _clean_html(
                    f"""
                    <div class="ha-review-line">
                      <span>{_safe(label)}</span>
                      <span>{value_html}</span>
                    </div>
                    """
                )
            )
    else:
        for item in lines or ["Unavailable"]:
            body.append(
                _clean_html(
                    f"""
                    <div class="ha-appeal-field">
                      <div class="ha-appeal-value">{_safe(item)}</div>
                    </div>
                    """
                )
            )
    _render_html(
        f"""
        <div class="ha-appeal-card">
          <h3>{_safe(title)}</h3>
          {''.join(body)}
        </div>
        """
    )


def _render_appeal_workspace(record, case, review, appeal, used_ai: bool) -> None:
    evidence = get_case_service().list_evidence(record.case_id) if record else []
    selected_count = len(set(appeal.evidence_ids)) if appeal else 0
    evidence_rows = _appeal_evidence_rows(appeal, evidence)
    citations = _appeal_citations(appeal)
    verification = appeal.verification.status.value if appeal else "NOT_RUN"
    human_state = "done" if verification == "APPROVED" else "active" if verification != "NOT_RUN" else ""
    flow_steps = [
        ("Case & Review", "done"),
        ("Evidence Selection", "done"),
        ("Letter Generation", "active"),
        ("Human Review", human_state),
        ("Export", ""),
    ]
    flow_html = "".join(
        f'<div class="ha-flow-step {state}"><div class="ha-flow-dot">{_safe("OK" if state == "done" else index)}</div><div>{_safe(label)}</div></div>'
        for index, (label, state) in enumerate(flow_steps, start=1)
    )
    _render_html(
        f"""
        <div class="ha-appeal-shell">
          <div class="ha-appeal-titlebar">
            <div>
              <h2>Appeal Generator</h2>
              <div class="ha-muted">Generate a customized appeal letter using the case details, clinical review results, and supporting evidence.</div>
            </div>
            <div class="ha-appeal-actions">
              <span class="ha-action-ghost">Regenerate Letter</span>
              <span class="ha-action-ghost">More Actions</span>
            </div>
          </div>
          <div class="ha-appeal-flow">{flow_html}</div>
        </div>
        """
    )

    left, middle, right = st.columns([0.82, 0.96, 1.9])
    with left:
        _render_appeal_card("Case Summary", _appeal_case_summary(case, record, appeal))
        _render_appeal_card("Review Summary", _appeal_review_lines(review))
        _render_appeal_card("Appeal Strategy", _appeal_strategy_lines(appeal))
    with middle:
        evidence_html = "".join(
            _clean_html(
                f"""
                <div class="ha-evidence-row">
                  <div class="ha-check">OK</div>
                  <div>
                    <div style="font-weight:750;color:#f8fafc;">{_safe(title)}</div>
                    <div class="ha-muted" style="font-size:0.8rem;line-height:1.35;">{_safe(desc)}</div>
                    <div style="margin-top:0.35rem;"><span class="ha-quality-chip {quality.lower()}">{_safe(quality)}</span></div>
                  </div>
                  <div class="ha-page-tag">{_safe(citation)}</div>
                </div>
                """
            )
            for title, desc, citation, quality in evidence_rows
        )
        citation_html = "".join(
            _clean_html(
                f"""
                <div class="ha-review-line">
                  <span>{_safe(citation)}</span>
                  <span></span>
                </div>
                """
            )
            for citation in citations
        )
        _render_html(
            f"""
            <div class="ha-appeal-card">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:1rem;">
                <h3>Selected Supporting Evidence</h3>
                <span class="ha-count-chip">{selected_count or len(evidence_rows)} selected</span>
              </div>
              {evidence_html}
              <div class="ha-upload-box">
                <div class="ha-upload-icon">UP</div>
                <div>
                  <div style="font-weight:700;color:#f8fafc;">Add additional evidence</div>
                  <div class="ha-muted" style="font-size:0.8rem;">Drag files here or click to browse</div>
                  <div class="ha-muted" style="font-size:0.75rem;margin-top:0.2rem;">PDF, TXT, PNG (max 20MB)</div>
                </div>
              </div>
            </div>
            <div class="ha-appeal-card">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:1rem;">
                <h3>Citations in Letter</h3>
                <span class="ha-count-chip">{len(citations)}</span>
              </div>
              {citation_html}
              <div style="margin-top:0.8rem;">
                <span class="ha-action-ghost">View Citation Map</span>
              </div>
            </div>
            """
        )
    with right:
        letter_body = appeal.letter_text if appeal else "No appeal letter has been generated yet."
        _render_html(
            f"""
            <div class="ha-letter-panel">
              <div class="ha-letter-head">
                <div>
                  <div class="ha-letter-title">Appeal Letter (Editable)</div>
                  <div class="ha-muted" style="font-size:0.78rem;margin-top:0.15rem;">{'AI backend' if used_ai else 'Letter builder'} draft</div>
                </div>
                <div class="ha-action-ghost">Insert Placeholder</div>
              </div>
              <div class="ha-editor-toolbar">
                <span>Undo</span><span>Redo</span><span class="ha-tool-divider"></span><span>Normal</span><span class="ha-tool-divider"></span><span>B</span><span>I</span><span>U</span><span>List</span><span>Link</span>
              </div>
              <div class="ha-letter-body">{_safe(letter_body)}</div>
              <div class="ha-letter-foot">
                <span>Word Count: {len((letter_body or '').split())}</span>
                <span class="ha-green">All required elements present</span>
              </div>
            </div>
            """
        )
        stem = _selected_case_stem()
        action_col1, action_col2, action_col3 = st.columns([1, 1, 1.35])
        action_col1.download_button(
            label="Preview TXT",
            data=appeal.to_txt(),
            file_name=f"{stem}_appeal.txt",
            mime="text/plain",
            key="appeal_download_txt",
        )
        action_col2.download_button(
            label="Save Draft",
            data=appeal.to_markdown(),
            file_name=f"{stem}_appeal.md",
            mime="text/markdown",
            key="appeal_download_md",
        )
        action_col3.button(
            "Submit for Human Review",
            type="primary",
            key="appeal_submit_review",
            on_click=_set_nav,
            args=("Human Review",),
        )

    _render_html(
        f"""
        <div class="ha-appeal-note">
          <div class="ha-info-dot">i</div>
          <div>
            <div style="font-weight:700;color:#f8fafc;">Important</div>
            <div class="ha-muted">This letter is generated based on selected evidence and review results. Please verify all information before submission.</div>
          </div>
          <div class="ha-muted">Generated by: {'AI backend' if used_ai else 'Deterministic Engine'}</div>
        </div>
        """
    )
    with st.expander("Structured appeal JSON"):
        st.json(appeal.model_dump(mode="json"))


def _persist_appeal_case() -> None:
    case_id = case_ui.persist_current_case()
    if case_id:
        st.caption(
            f"Saved to case **{case_id}** - now pending human review. See the "
            "Case Management and Human Review tabs."
        )


# --------------------------------------------------------------------------- #
# Designer-inspired cockpit shell
# --------------------------------------------------------------------------- #
_NAV_KEY = "healthai_nav"


def _clean_html(markup: str) -> str:
    """Remove Python indentation so Markdown does not treat HTML as code."""
    dedented = textwrap.dedent(markup).strip()
    return "\n".join(line.lstrip() for line in dedented.splitlines() if line.strip())


def _render_html(markup: str) -> None:
    cleaned = _clean_html(markup)
    render_html = getattr(st, "html", None)
    if render_html is not None:
        render_html(cleaned)
    else:
        st.markdown(cleaned, unsafe_allow_html=True)


def _render_sidebar_html(markup: str) -> None:
    cleaned = _clean_html(markup)
    render_html = getattr(st, "html", None)
    if render_html is not None:
        with st.sidebar:
            render_html(cleaned)
    else:
        st.sidebar.markdown(cleaned, unsafe_allow_html=True)


def _handle_shell_case_change(widget_key: str, current_upload_label: str) -> None:
    choice = st.session_state.get(widget_key)
    session.set_persisted_case_id(None if choice == current_upload_label else choice)


def _nav_options() -> list[str]:
    return [
        "Structured Extraction",
        "Case Intake & Assembly",
        "Clinical Review",
        "Appeal Generator",
        "Reviewer Workbench",
        "Operations & Governance",
        "Cases",
        "Reports & Analytics",
        "My Review Queue",
        "Detailed Clinical Review",
        "Pending Conflicts",
    ]


def _inject_cockpit_css() -> None:
    """Apply the dark HealthAI cockpit visual system."""
    _render_html(
        """
        <style>
        :root {
          --ha-bg: #080d16;
          --ha-panel: #111827;
          --ha-panel-2: #151e2e;
          --ha-line: rgba(148, 163, 184, 0.18);
          --ha-muted: #9aa7b8;
          --ha-text: #f8fafc;
          --ha-blue: #4f7cff;
          --ha-green: #36c86a;
          --ha-warn: #f59e0b;
          --ha-red: #ef4444;
          --ha-purple: #8b5cf6;
        }

        .stApp {
          background:
            radial-gradient(circle at 78% 2%, rgba(79, 124, 255, 0.13), transparent 30rem),
            radial-gradient(circle at 24% 18%, rgba(16, 185, 129, 0.08), transparent 24rem),
            var(--ha-bg);
          color: var(--ha-text);
        }

        [data-testid="stHeader"], [data-testid="stToolbar"],
        [data-testid="stDecoration"], footer {
          display: none !important;
        }

        [data-testid="stSidebar"] {
          background:
            linear-gradient(180deg, rgba(17, 24, 39, 0.98), rgba(10, 15, 25, 0.98));
          border-right: 1px solid var(--ha-line);
        }

        [data-testid="stSidebar"] * {
          color: #e5edf8;
        }

        .block-container {
          max-width: 1680px;
          padding-top: 1.35rem;
          padding-bottom: 1.8rem;
        }

        div[data-testid="stMetric"] {
          background: linear-gradient(180deg, rgba(22, 32, 50, 0.92), rgba(14, 21, 34, 0.96));
          border: 1px solid var(--ha-line);
          border-radius: 10px;
          padding: 1rem;
        }

        div.stButton > button,
        div.stDownloadButton > button {
          border-radius: 8px;
          border: 1px solid rgba(148, 163, 184, 0.28);
          background: linear-gradient(180deg, rgba(42, 53, 76, 0.95), rgba(23, 32, 49, 0.98));
          color: #f8fafc;
          min-height: 2.4rem;
        }

        div.stButton > button[kind="primary"],
        div.stDownloadButton > button[kind="primary"] {
          background: linear-gradient(135deg, #f59e0b, #ea580c);
          border-color: rgba(251, 191, 36, 0.72);
          box-shadow: 0 0 22px rgba(245, 158, 11, 0.24);
        }

        .ha-brand {
          display: flex;
          align-items: center;
          gap: 0.72rem;
          padding: 0.35rem 0.1rem 1.05rem;
        }

        .ha-logo {
          width: 2.15rem;
          height: 2.15rem;
          border-radius: 0.72rem;
          display: grid;
          place-items: center;
          font-weight: 900;
          color: white;
          background: linear-gradient(135deg, #3b82f6, #8b5cf6);
          box-shadow: 0 0 28px rgba(79, 124, 255, 0.35);
        }

        .ha-brand-title {
          font-size: 1.26rem;
          font-weight: 750;
          line-height: 1.05;
        }

        .ha-brand-sub {
          color: var(--ha-muted);
          font-size: 0.78rem;
          margin-top: 0.18rem;
        }

        .ha-side-caption {
          color: var(--ha-muted);
          font-size: 0.72rem;
          letter-spacing: 0.07em;
          text-transform: uppercase;
          margin: 1.15rem 0 0.35rem;
        }

        .ha-topbar {
          display: grid;
          grid-template-columns: minmax(18rem, 42rem) 1fr auto;
          gap: 1rem;
          align-items: center;
          margin-bottom: 1.15rem;
        }

        .ha-search {
          height: 2.65rem;
          border-radius: 9px;
          border: 1px solid var(--ha-line);
          background: rgba(17, 24, 39, 0.92);
          display: flex;
          align-items: center;
          gap: 0.75rem;
          padding: 0 1rem;
          color: var(--ha-muted);
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
        }

        .ha-user {
          display: flex;
          align-items: center;
          gap: 0.75rem;
          color: var(--ha-muted);
          justify-self: end;
        }

        .ha-avatar {
          width: 2.25rem;
          height: 2.25rem;
          border-radius: 999px;
          background: linear-gradient(135deg, #f59e0b, #8b5cf6);
          display: grid;
          place-items: center;
          color: white;
          font-weight: 800;
          border: 2px solid rgba(255,255,255,0.22);
        }

        .ha-shell-card,
        .ha-card,
        .ha-case-header {
          border: 1px solid var(--ha-line);
          background:
            linear-gradient(180deg, rgba(20, 30, 48, 0.94), rgba(12, 18, 30, 0.96));
          box-shadow: 0 14px 34px rgba(0,0,0,0.22), inset 0 1px 0 rgba(255,255,255,0.04);
        }

        .ha-case-header {
          border-radius: 11px;
          margin-bottom: 0.62rem;
          overflow: hidden;
        }

        .ha-case-main {
          display: grid;
          grid-template-columns: minmax(18rem, 2.2fr) minmax(12rem, 0.7fr) minmax(12rem, 0.7fr) auto;
          gap: 1.25rem;
          align-items: center;
          padding: 1.35rem 1.55rem;
        }

        .ha-case-title {
          font-size: 1.55rem;
          font-weight: 780;
          letter-spacing: 0;
          margin-bottom: 0.45rem;
        }

        .ha-case-meta,
        .ha-kicker,
        .ha-subtle,
        .ha-card-text {
          color: var(--ha-muted);
        }

        .ha-badge {
          display: inline-flex;
          align-items: center;
          gap: 0.35rem;
          border-radius: 999px;
          padding: 0.26rem 0.55rem;
          font-size: 0.7rem;
          font-weight: 800;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          border: 1px solid rgba(245, 158, 11, 0.33);
          color: #fbbf24;
          background: rgba(245, 158, 11, 0.12);
        }

        .ha-service-label {
          color: var(--ha-muted);
          font-size: 0.76rem;
          margin-bottom: 0.35rem;
        }

        .ha-service-value {
          color: var(--ha-text);
          font-size: 1rem;
        }

        .ha-payer {
          color: #a78bfa;
          font-weight: 850;
          font-size: 1.45rem;
          letter-spacing: 0;
        }

        .ha-stepper {
          display: grid;
          grid-template-columns: repeat(7, minmax(7.5rem, 1fr));
          gap: 0.75rem;
          padding: 1.1rem 1.35rem 1.25rem;
          border-top: 1px solid var(--ha-line);
          background: rgba(15, 23, 42, 0.68);
        }

        .ha-step {
          min-width: 0;
          display: grid;
          grid-template-columns: 1.65rem 1fr;
          gap: 0.55rem;
          align-items: start;
        }

        .ha-dot {
          width: 1.45rem;
          height: 1.45rem;
          border-radius: 999px;
          display: grid;
          place-items: center;
          font-size: 0.72rem;
          font-weight: 800;
          border: 1px solid rgba(148, 163, 184, 0.45);
          color: #cbd5e1;
          background: rgba(15, 23, 42, 0.94);
        }

        .ha-step.done .ha-dot { background: #22a95b; border-color: #22a95b; color: white; }
        .ha-step.ready .ha-dot {
          background: linear-gradient(135deg, #f59e0b, #fb923c);
          border-color: #fbbf24;
          color: #111827;
          box-shadow: 0 0 20px rgba(245, 158, 11, 0.52);
        }
        .ha-step.active .ha-dot { background: #3b82f6; border-color: #75a7ff; color: white; }
        .ha-step.ready .ha-step-title { color: #fbbf24; }

        .ha-step-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 0.32rem;
          margin-top: 0.35rem;
        }

        .ha-mini-pill {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          border: 1px solid rgba(251, 191, 36, 0.38);
          background: rgba(245, 158, 11, 0.12);
          color: #fcd34d;
          font-size: 0.66rem;
          font-weight: 800;
          padding: 0.12rem 0.42rem;
        }

        .ha-step-title {
          font-size: 0.84rem;
          color: #f8fafc;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .ha-step-sub {
          color: var(--ha-muted);
          font-size: 0.76rem;
          margin-top: 0.18rem;
        }

        .ha-intake-shell {
          display: grid;
          gap: 1rem;
        }

        .ha-intake-hero {
          display: flex;
          justify-content: space-between;
          gap: 1rem;
          align-items: flex-start;
          flex-wrap: wrap;
        }

        .ha-intake-title {
          font-size: 1.55rem;
          font-weight: 780;
          line-height: 1.12;
          margin: 0;
        }

        .ha-intake-sub {
          color: var(--ha-muted);
          font-size: 0.88rem;
          margin-top: 0.25rem;
          max-width: 52rem;
        }

        .ha-intake-meta {
          display: flex;
          gap: 0.55rem;
          flex-wrap: wrap;
          justify-content: flex-end;
        }

        .ha-intake-chip {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 0.3rem 0.62rem;
          font-size: 0.72rem;
          font-weight: 800;
          letter-spacing: 0.02em;
          border: 1px solid rgba(148, 163, 184, 0.24);
          background: rgba(15, 23, 42, 0.56);
          color: #dbeafe;
        }

        .ha-intake-stepper {
          display: grid;
          grid-template-columns: repeat(5, minmax(0, 1fr));
          gap: 0.65rem;
        }

        .ha-intake-step {
          min-width: 0;
          display: grid;
          grid-template-columns: 1.4rem 1fr;
          gap: 0.6rem;
          align-items: start;
          padding: 0.85rem 0.9rem;
          border-radius: 9px;
          border: 1px solid rgba(148, 163, 184, 0.14);
          background: rgba(15, 23, 42, 0.44);
        }

        .ha-intake-step.done {
          border-color: rgba(34, 197, 94, 0.34);
          background: rgba(16, 40, 28, 0.42);
        }

        .ha-intake-step.active {
          border-color: rgba(59, 130, 246, 0.38);
          background: rgba(11, 30, 57, 0.48);
        }

        .ha-intake-step.warn {
          border-color: rgba(245, 158, 11, 0.42);
          background: rgba(58, 34, 8, 0.4);
        }

        .ha-intake-dot {
          width: 1.45rem;
          height: 1.45rem;
          border-radius: 999px;
          display: grid;
          place-items: center;
          border: 1px solid rgba(148, 163, 184, 0.4);
          color: #cbd5e1;
          background: rgba(15, 23, 42, 0.94);
          font-size: 0.72rem;
          font-weight: 850;
        }

        .ha-intake-step.done .ha-intake-dot {
          background: #22a95b;
          border-color: #22a95b;
          color: white;
        }

        .ha-intake-step.active .ha-intake-dot {
          background: #3b82f6;
          border-color: #75a7ff;
          color: white;
        }

        .ha-intake-step.warn .ha-intake-dot {
          background: #f59e0b;
          border-color: #f59e0b;
          color: #111827;
        }

        .ha-intake-step-title {
          color: #f8fafc;
          font-size: 0.86rem;
          font-weight: 750;
          line-height: 1.2;
        }

        .ha-intake-step-sub {
          color: var(--ha-muted);
          font-size: 0.75rem;
          margin-top: 0.2rem;
        }

        .ha-intake-grid {
          display: grid;
          grid-template-columns: minmax(20rem, 0.96fr) minmax(23rem, 1.08fr) minmax(28rem, 1.3fr);
          gap: 0.85rem;
          align-items: start;
        }

        .ha-doc-list {
          display: grid;
          gap: 0.72rem;
        }

        .ha-doc-card {
          border-radius: 9px;
          padding: 0.9rem;
          border: 1px solid rgba(148, 163, 184, 0.14);
          background: rgba(15, 23, 42, 0.42);
        }

        .ha-doc-card.active {
          border-color: rgba(59, 130, 246, 0.72);
          box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.22) inset;
        }

        .ha-doc-top {
          display: flex;
          justify-content: space-between;
          gap: 0.75rem;
          align-items: flex-start;
        }

        .ha-doc-name {
          color: #f8fafc;
          font-size: 0.92rem;
          font-weight: 770;
          line-height: 1.2;
        }

        .ha-doc-badges {
          display: flex;
          gap: 0.35rem;
          flex-wrap: wrap;
          justify-content: flex-end;
        }

        .ha-doc-meta {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 0.55rem;
          margin-top: 0.75rem;
        }

        .ha-doc-metric {
          border-radius: 7px;
          border: 1px solid rgba(148, 163, 184, 0.11);
          background: rgba(12, 18, 30, 0.6);
          padding: 0.55rem 0.6rem;
        }

        .ha-doc-metric-label {
          color: var(--ha-muted);
          font-size: 0.68rem;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }

        .ha-doc-metric-value {
          color: #f8fafc;
          font-size: 0.8rem;
          font-weight: 720;
          margin-top: 0.25rem;
          line-height: 1.25;
        }

        .ha-doc-upload-box {
          border: 1px dashed rgba(59, 130, 246, 0.56);
          background: rgba(37, 99, 235, 0.08);
          border-radius: 9px;
          padding: 0.95rem;
        }

        .ha-doc-actions {
          display: flex;
          gap: 0.55rem;
          flex-wrap: wrap;
          margin-top: 0.85rem;
        }

        .ha-intake-panel {
          border-radius: 9px;
          padding: 1rem;
          margin-bottom: 1rem;
        }

        .ha-fact-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 0.6rem;
        }

        .ha-fact-card {
          border-radius: 8px;
          padding: 0.8rem;
          border: 1px solid rgba(148, 163, 184, 0.12);
          background: rgba(15, 23, 42, 0.42);
        }

        .ha-fact-label {
          color: var(--ha-muted);
          font-size: 0.72rem;
        }

        .ha-fact-value {
          color: #f8fafc;
          font-size: 0.88rem;
          font-weight: 780;
          line-height: 1.3;
          margin-top: 0.25rem;
        }

        .ha-fact-sub {
          color: var(--ha-muted);
          font-size: 0.72rem;
          margin-top: 0.24rem;
        }

        .ha-conflict-card {
          border-radius: 9px;
          border: 1px solid rgba(245, 158, 11, 0.28);
          background: linear-gradient(180deg, rgba(58, 34, 8, 0.42), rgba(19, 29, 46, 0.82));
          padding: 1rem;
        }

        .ha-conflict-values {
          display: grid;
          gap: 0.5rem;
          margin-top: 0.65rem;
        }

        .ha-conflict-value {
          border-radius: 7px;
          border: 1px solid rgba(148, 163, 184, 0.12);
          background: rgba(15, 23, 42, 0.44);
          padding: 0.65rem 0.72rem;
          color: #dbeafe;
          font-size: 0.8rem;
          line-height: 1.35;
        }

        .ha-preview-shell {
          border: 1px solid var(--ha-line);
          border-radius: 10px;
          background:
            linear-gradient(180deg, rgba(18, 28, 45, 0.96), rgba(9, 15, 26, 0.98));
          box-shadow: 0 18px 44px rgba(0,0,0,0.24), inset 0 1px 0 rgba(255,255,255,0.04);
          padding: 1rem;
        }

        .ha-preview-head {
          display: flex;
          justify-content: space-between;
          gap: 1rem;
          align-items: flex-start;
          margin-bottom: 0.85rem;
        }

        .ha-preview-title {
          font-size: 1rem;
          font-weight: 780;
          line-height: 1.2;
        }

        .ha-preview-sub {
          color: var(--ha-muted);
          font-size: 0.78rem;
          margin-top: 0.2rem;
        }

        .ha-preview-text {
          white-space: pre-wrap;
          color: #e5edf8;
          line-height: 1.58;
          font-size: 0.9rem;
          min-height: 14rem;
        }

        .ha-preview-search {
          margin-bottom: 0.75rem;
        }

        .ha-assembly-footer {
          display: flex;
          justify-content: flex-end;
          gap: 0.65rem;
          flex-wrap: wrap;
        }

        .ha-card {
          border-radius: 9px;
          padding: 1rem;
          margin-bottom: 1rem;
        }

        .ha-card h3 {
          font-size: 1rem;
          line-height: 1.2;
          margin: 0 0 0.85rem;
        }

        .ha-summary-split {
          display: grid;
          grid-template-columns: 0.85fr 1.15fr;
          border: 1px solid var(--ha-line);
          border-radius: 8px;
          overflow: hidden;
          background: rgba(15, 23, 42, 0.42);
        }

        .ha-gauge-wrap {
          display: grid;
          place-items: center;
          min-height: 10.6rem;
          border-right: 1px solid var(--ha-line);
          padding: 1rem;
        }

        .ha-gauge {
          --value: 0%;
          width: 8.9rem;
          height: 8.9rem;
          border-radius: 999px;
          background: conic-gradient(from 220deg, #f59e0b 0 var(--value), #ef4444 var(--value) calc(var(--value) + 10%), #25314a 0 100%);
          display: grid;
          place-items: center;
          position: relative;
        }

        .ha-gauge::before {
          content: "";
          position: absolute;
          inset: 0.72rem;
          border-radius: 999px;
          background: #101827;
        }

        .ha-gauge-value {
          position: relative;
          z-index: 1;
          font-size: 2rem;
          font-weight: 850;
        }

        .ha-gauge-caption {
          position: relative;
          z-index: 1;
          color: var(--ha-muted);
          font-size: 0.75rem;
          margin-top: -1.15rem;
        }

        .ha-rec-panel {
          padding: 1rem;
          display: grid;
          gap: 0.8rem;
          align-content: center;
        }

        .ha-rec {
          color: #fbbf24;
          font-weight: 850;
          letter-spacing: 0.02em;
        }

        .ha-table {
          width: 100%;
          border-collapse: collapse;
          font-size: 0.82rem;
          overflow: hidden;
          border-radius: 8px;
          border: 1px solid var(--ha-line);
        }

        .ha-table th,
        .ha-table td {
          padding: 0.72rem 0.72rem;
          border-bottom: 1px solid rgba(148, 163, 184, 0.13);
          text-align: left;
          vertical-align: top;
        }

        .ha-table th {
          color: var(--ha-muted);
          font-weight: 600;
          background: rgba(15, 23, 42, 0.55);
        }

        .ha-status-met { color: #4ade80; font-weight: 750; }
        .ha-status-missing { color: #fb7185; font-weight: 750; }
        .ha-status-unknown { color: #fbbf24; font-weight: 750; }

        .ha-missing-item,
        .ha-met-item,
        .ha-evidence-item,
        .ha-action-item {
          border-radius: 8px;
          border: 1px solid rgba(148, 163, 184, 0.13);
          background: rgba(15, 23, 42, 0.38);
          padding: 0.85rem 0.9rem;
          margin-bottom: 0.65rem;
        }

        .ha-item-title {
          color: #f8fafc;
          font-size: 0.88rem;
          font-weight: 750;
          margin-bottom: 0.38rem;
        }

        .ha-pill {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 0.18rem 0.5rem;
          font-size: 0.7rem;
          font-weight: 700;
          background: rgba(59, 130, 246, 0.15);
          color: #93c5fd;
        }

        .ha-evidence-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 0.6rem;
          margin-bottom: 0.8rem;
        }

        .ha-stat {
          border-radius: 8px;
          border: 1px solid rgba(148, 163, 184, 0.13);
          background: rgba(15, 23, 42, 0.5);
          padding: 0.85rem;
        }

        .ha-stat-value {
          font-size: 1.55rem;
          font-weight: 850;
          line-height: 1.05;
        }

        .ha-stat-label {
          color: #cbd5e1;
          font-size: 0.78rem;
          margin-top: 0.35rem;
        }

        .ha-quality-bar {
          display: grid;
          grid-template-columns: var(--high, 1fr) var(--medium, 1fr) var(--low, 1fr);
          height: 0.42rem;
          overflow: hidden;
          border-radius: 999px;
          background: rgba(148, 163, 184, 0.18);
          margin: 0.8rem 0 0.65rem;
        }

        .ha-quality-bar div:nth-child(1) { background: var(--ha-green); }
        .ha-quality-bar div:nth-child(2) { background: var(--ha-warn); }
        .ha-quality-bar div:nth-child(3) { background: var(--ha-red); }

        .ha-legend {
          display: flex;
          gap: 0.9rem;
          flex-wrap: wrap;
          color: var(--ha-muted);
          font-size: 0.78rem;
        }

        .ha-legend span::before {
          content: "";
          display: inline-block;
          width: 0.5rem;
          height: 0.5rem;
          border-radius: 999px;
          margin-right: 0.35rem;
          vertical-align: middle;
          background: var(--dot);
        }

        .ha-footer-gate {
          display: grid;
          grid-template-columns: auto 1fr auto;
          align-items: center;
          gap: 1rem;
          border-radius: 9px;
          border: 1px solid rgba(239, 68, 68, 0.18);
          background: linear-gradient(90deg, rgba(127, 29, 29, 0.32), rgba(87, 34, 44, 0.34));
          padding: 0.95rem 1rem;
          margin-top: 0.3rem;
        }

        .ha-empty {
          border: 1px dashed rgba(148, 163, 184, 0.32);
          background: rgba(15, 23, 42, 0.48);
          border-radius: 9px;
          padding: 1.1rem;
          color: var(--ha-muted);
        }

        .ha-appeal-shell {
          border: 1px solid var(--ha-line);
          border-radius: 10px;
          background:
            linear-gradient(180deg, rgba(18, 28, 45, 0.96), rgba(9, 15, 26, 0.98));
          box-shadow: 0 18px 44px rgba(0,0,0,0.24), inset 0 1px 0 rgba(255,255,255,0.04);
          padding: 1.15rem;
          margin-bottom: 1rem;
        }

        .ha-appeal-titlebar {
          display: grid;
          grid-template-columns: minmax(16rem, 1fr) auto;
          align-items: start;
          gap: 1rem;
          margin-bottom: 1rem;
        }

        .ha-appeal-titlebar h2 {
          margin: 0 0 0.35rem;
          font-size: 1.55rem;
          line-height: 1.15;
          letter-spacing: 0;
        }

        .ha-appeal-actions {
          display: flex;
          gap: 0.6rem;
          justify-content: flex-end;
          flex-wrap: wrap;
        }

        .ha-action-ghost {
          border: 1px solid rgba(148, 163, 184, 0.24);
          background: rgba(30, 41, 59, 0.58);
          color: #e5edf8;
          border-radius: 7px;
          padding: 0.48rem 0.72rem;
          font-size: 0.82rem;
          font-weight: 650;
        }

        .ha-appeal-flow {
          display: grid;
          grid-template-columns: repeat(5, minmax(8rem, 1fr));
          gap: 0.5rem;
          align-items: center;
          margin: 1rem 0 1.25rem;
        }

        .ha-flow-step {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          min-width: 0;
          color: #cbd5e1;
          font-size: 0.82rem;
          font-weight: 700;
        }

        .ha-flow-dot {
          width: 1.45rem;
          height: 1.45rem;
          border-radius: 999px;
          display: grid;
          place-items: center;
          flex: 0 0 auto;
          border: 1px solid rgba(148, 163, 184, 0.45);
          background: rgba(15, 23, 42, 0.94);
          color: #cbd5e1;
          font-size: 0.75rem;
          font-weight: 850;
        }

        .ha-flow-step.done .ha-flow-dot {
          background: #22a95b;
          border-color: #22a95b;
          color: white;
        }

        .ha-flow-step.active .ha-flow-dot {
          background: #3b82f6;
          border-color: #75a7ff;
          color: white;
          box-shadow: 0 0 20px rgba(59, 130, 246, 0.36);
        }

        .ha-appeal-grid {
          display: grid;
          grid-template-columns: minmax(15rem, 0.72fr) minmax(17rem, 0.86fr) minmax(28rem, 1.9fr);
          gap: 0.85rem;
          align-items: start;
        }

        .ha-appeal-col {
          display: grid;
          gap: 0.85rem;
        }

        .ha-appeal-card,
        .ha-letter-panel,
        .ha-letter-actions,
        .ha-appeal-note {
          border: 1px solid var(--ha-line);
          background: linear-gradient(180deg, rgba(19, 29, 46, 0.82), rgba(10, 17, 29, 0.92));
          border-radius: 8px;
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.035);
        }

        .ha-appeal-card {
          padding: 1rem;
        }

        .ha-appeal-card h3,
        .ha-letter-title {
          margin: 0;
          font-size: 0.98rem;
          line-height: 1.25;
        }

        .ha-appeal-field {
          margin-top: 0.95rem;
        }

        .ha-appeal-label {
          color: var(--ha-muted);
          font-size: 0.78rem;
          margin-bottom: 0.25rem;
        }

        .ha-appeal-value {
          color: #f8fafc;
          font-size: 0.88rem;
          line-height: 1.35;
        }

        .ha-denial-box {
          margin-top: 0.45rem;
          border-radius: 7px;
          background: rgba(127, 29, 29, 0.34);
          border: 1px solid rgba(248, 113, 113, 0.18);
          padding: 0.75rem;
          color: #fecaca;
          font-size: 0.82rem;
          line-height: 1.42;
        }

        .ha-review-line {
          display: flex;
          justify-content: space-between;
          gap: 1rem;
          border-bottom: 1px solid rgba(148, 163, 184, 0.12);
          padding: 0.48rem 0;
          color: #dbeafe;
          font-size: 0.83rem;
        }

        .ha-review-line:last-child {
          border-bottom: 0;
        }

        .ha-status-chip,
        .ha-count-chip,
        .ha-quality-chip {
          display: inline-flex;
          align-items: center;
          border-radius: 999px;
          padding: 0.18rem 0.48rem;
          font-size: 0.68rem;
          font-weight: 800;
          letter-spacing: 0.02em;
          text-transform: uppercase;
        }

        .ha-status-chip {
          color: #fcd34d;
          background: rgba(245, 158, 11, 0.14);
          border: 1px solid rgba(245, 158, 11, 0.28);
        }

        .ha-count-chip {
          color: #93c5fd;
          background: rgba(59, 130, 246, 0.16);
          border: 1px solid rgba(59, 130, 246, 0.26);
        }

        .ha-quality-chip.high {
          color: #86efac;
          background: rgba(34, 197, 94, 0.14);
        }

        .ha-quality-chip.medium {
          color: #fcd34d;
          background: rgba(245, 158, 11, 0.14);
        }

        .ha-evidence-row {
          display: grid;
          grid-template-columns: 1.2rem 1fr auto;
          gap: 0.72rem;
          padding: 0.85rem 0;
          border-bottom: 1px solid rgba(148, 163, 184, 0.13);
        }

        .ha-evidence-row:last-child {
          border-bottom: 0;
          padding-bottom: 0;
        }

        .ha-check {
          width: 1rem;
          height: 1rem;
          border-radius: 4px;
          display: grid;
          place-items: center;
          background: #2f7df6;
          color: white;
          font-size: 0.68rem;
          font-weight: 900;
          margin-top: 0.14rem;
        }

        .ha-page-tag {
          color: #cbd5e1;
          font-size: 0.72rem;
          white-space: nowrap;
        }

        .ha-upload-box {
          display: grid;
          grid-template-columns: 2rem 1fr;
          gap: 0.75rem;
          align-items: center;
          border: 1px dashed rgba(59, 130, 246, 0.62);
          background: rgba(37, 99, 235, 0.08);
          border-radius: 8px;
          padding: 0.95rem;
          margin-top: 0.85rem;
        }

        .ha-upload-icon {
          width: 2rem;
          height: 2rem;
          border-radius: 7px;
          display: grid;
          place-items: center;
          background: rgba(59, 130, 246, 0.18);
          color: #93c5fd;
          font-weight: 900;
        }

        .ha-letter-panel {
          overflow: hidden;
        }

        .ha-letter-head {
          display: flex;
          justify-content: space-between;
          gap: 1rem;
          align-items: center;
          padding: 0.95rem 1rem;
          border-bottom: 1px solid var(--ha-line);
        }

        .ha-editor-toolbar {
          display: flex;
          gap: 0.85rem;
          align-items: center;
          color: #cbd5e1;
          border-bottom: 1px solid rgba(148, 163, 184, 0.13);
          background: rgba(15, 23, 42, 0.42);
          padding: 0.72rem 1rem;
          font-size: 0.82rem;
        }

        .ha-tool-divider {
          width: 1px;
          height: 1.35rem;
          background: rgba(148, 163, 184, 0.16);
        }

        .ha-letter-body {
          white-space: pre-wrap;
          padding: 1.25rem 1.35rem;
          min-height: 29rem;
          color: #f8fafc;
          line-height: 1.62;
          font-size: 0.92rem;
        }

        .ha-letter-foot {
          display: flex;
          justify-content: flex-end;
          gap: 1rem;
          align-items: center;
          padding: 0.65rem 1rem;
          border-top: 1px solid rgba(148, 163, 184, 0.13);
          color: var(--ha-muted);
          font-size: 0.76rem;
        }

        .ha-letter-actions {
          display: grid;
          grid-template-columns: 1fr 1fr 1.35fr;
          gap: 0.75rem;
          padding: 0.75rem;
          margin-top: 0.35rem;
        }

        .ha-appeal-note {
          display: grid;
          grid-template-columns: 1.6rem 1fr auto;
          gap: 0.85rem;
          align-items: center;
          padding: 0.95rem 1rem;
        }

        .ha-info-dot {
          width: 1.35rem;
          height: 1.35rem;
          border-radius: 999px;
          border: 1px solid rgba(59, 130, 246, 0.7);
          color: #60a5fa;
          display: grid;
          place-items: center;
          font-weight: 850;
        }

        .ha-muted { color: var(--ha-muted); }
        .ha-green { color: var(--ha-green); }
        .ha-warn { color: var(--ha-warn); }
        .ha-red { color: var(--ha-red); }

        @media (max-width: 1100px) {
          .ha-topbar,
          .ha-case-main,
          .ha-summary-split,
          .ha-appeal-titlebar,
          .ha-appeal-grid,
          .ha-intake-grid,
          .ha-letter-actions,
          .ha-appeal-note {
            grid-template-columns: 1fr;
          }
          .ha-intake-stepper,
          .ha-fact-grid,
          .ha-doc-meta {
            grid-template-columns: 1fr;
          }
          .ha-stepper {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
          .ha-appeal-flow {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
          .ha-gauge-wrap {
            border-right: 0;
            border-bottom: 1px solid var(--ha-line);
          }
        }
        </style>
        """
    )


def _safe(text: object) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def _short(text: object, limit: int = 118) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _percent(value: float | None) -> int:
    return round(max(0.0, min(1.0, float(value or 0.0))) * 100)


def _status_text(value: object) -> str:
    return str(getattr(value, "value", value or "")).replace("_", " ").title()


def _set_nav(label: str) -> None:
    st.session_state[_NAV_KEY] = _normalize_nav(label)


def _normalize_nav(label: str) -> str:
    aliases = {
        "Document Ingestion": "Case Intake & Assembly",
        "Document Assembly": "Case Intake & Assembly",
        "OCR Status": "Case Intake & Assembly",
        "Evidence Explorer": "Case Intake & Assembly",
        "Evidence Quality": "Reviewer Workbench",
        "Conflict Review": "Pending Conflicts",
        "Conflict Resolution": "Pending Conflicts",
        "Reviewer Feedback": "Reviewer Workbench",
        "Governance Settings": "Operations & Governance",
        "Guidelines & Policies": "Operations & Governance",
        "Operational Health": "Operations & Governance",
        "Validation Runner": "Operations & Governance",
        "Audit Log": "Reports & Analytics",
        "Quality Analytics": "Reports & Analytics",
        "Review Explainability": "Reports & Analytics",
        "Appeal Explainability": "Reports & Analytics",
        "All Cases": "Cases",
        "Case Management": "Cases",
        "Human Review": "Reviewer Workbench",
    }
    return aliases.get(label, label)


def _case_for_shell(service):
    """Return the explicitly selected persisted case, if any."""
    cases = service.list_cases()
    selected = session.get_persisted_case_id()
    record = service.get_case(selected) if selected else None
    return record, cases


def _active_case_artifacts(service):
    record, cases = _case_for_shell(service)
    case = record.patient_case if record and record.patient_case else session.get_case()
    review = (
        record.review_result if record and record.review_result else session.get_review()
    )
    appeal = (
        record.appeal_letter if record and record.appeal_letter else session.get_appeal()
    )
    docs = service.list_documents(record.case_id) if record else []
    evidence = service.list_evidence(record.case_id) if record else []
    quality = service.list_evidence_quality(record.case_id) if record else []
    decisions = service.list_evidence_decisions(record.case_id) if record else []
    return record, cases, case, review, appeal, docs, evidence, quality, decisions


def _latest_decisions_by_evidence(decisions):
    latest = {}
    for decision in decisions:
        latest[decision.evidence_id] = decision
    return latest


def _quality_counts(evidence, quality):
    scores = [q.overall_score for q in quality] if quality else [
        e.confidence_score for e in evidence
    ]
    high = sum(1 for score in scores if score >= 0.95)
    medium = sum(1 for score in scores if 0.70 <= score < 0.95)
    low = sum(1 for score in scores if score < 0.70)
    return high, medium, low


_INTAKE_UPLOAD_TYPES = ["txt", "pdf", "png", "jpg", "jpeg"]
_INTAKE_TEXT_OR_PDF_TYPES = ["txt", "pdf"]


def _friendly_doc_type(value: object) -> str:
    raw = str(getattr(value, "value", value or "OTHER"))
    return raw.replace("_", " ").title()


def _friendly_fact_label(value: object) -> str:
    return str(value or "Unknown").replace("_", " ").title()


def _ocr_badge_class(status: str) -> str:
    low = status.lower()
    if "unavailable" in low or "low" in low:
        return "ha-warn"
    if "ocr used" in low or "text layer" in low or "txt" in low:
        return "ha-green"
    return "ha-muted"


def _document_status_detail(status) -> tuple[str, str]:
    if status is None:
        return "Not checked", "No OCR status is available yet."
    return status.status, status.detail


def _format_uploaded_at(value: str | None) -> str:
    if not value:
        return "Uploaded time unavailable"
    return value.replace("T", " ")[:16]


def _doc_evidence_counts(evidence) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ref in evidence:
        counts[ref.source_document_id] = counts.get(ref.source_document_id, 0) + 1
    return counts


def _doc_conflict_counts(context) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not context:
        return counts
    evidence_by_id = {ref.evidence_id: ref for ref in context.evidence}
    for conflict in context.conflict_report.conflicts:
        seen_docs = {
            evidence_by_id[eid].source_document_id
            for eid in conflict.evidence_ids
            if eid in evidence_by_id
        }
        for doc_id in seen_docs:
            counts[doc_id] = counts.get(doc_id, 0) + 1
    return counts


def _context_from_evidence(service, case_id: str | None, docs, evidence):
    if not case_id or not evidence:
        return None
    try:
        return service.assembly.synthesize_from_evidence(case_id, evidence, docs)
    except Exception:  # noqa: BLE001 - summary UI should degrade gracefully
        return None


def _intake_case_title(record, case) -> str:
    if record and case:
        patient = case.patient_name or "Unknown patient"
        service_name = case.requested_service or "unspecified service"
        return f"{service_name} - {patient}"
    if record:
        return record.display_name()
    return "Select or create a case"


def _render_intake_stepper(docs, statuses, evidence, context, review) -> None:
    total_docs = len(docs)
    docs_with_text = sum(1 for doc in docs if (doc.raw_text or "").strip())
    conflict_count = (
        len(context.conflict_report.conflicts)
        if context and context.conflict_report
        else 0
    )
    missing_count = len(context.missing_information) if context else 0
    status_values = [s.status.lower() for s in statuses.values()]
    ocr_unavailable = any("unavailable" in value for value in status_values)
    ocr_done = bool(docs) and docs_with_text == total_docs and not ocr_unavailable
    ocr_sub = f"{docs_with_text} / {total_docs}" if docs else "Waiting"
    steps = [
        (
            "Documents Uploaded",
            "done" if docs else "active",
            f"{total_docs} document(s)" if docs else "Waiting",
            "OK" if docs else "1",
        ),
        (
            "OCR Complete",
            "done" if ocr_done else "warn" if ocr_unavailable else "active" if docs else "",
            ocr_sub,
            "OK" if ocr_done else "!",
        ),
        (
            "Evidence Assembled",
            "done" if evidence else "active" if docs else "",
            f"{len(evidence)} item(s)" if evidence else "Waiting",
            "OK" if evidence else "3",
        ),
        (
            "Conflicts Detected",
            "warn" if conflict_count else "done" if evidence else "",
            str(conflict_count) if evidence else "Waiting",
            "!" if conflict_count else "OK" if evidence else "4",
        ),
        (
            "Review Pending",
            "done" if review else "active" if evidence else "",
            "Ready" if evidence and not review else "Complete" if review else "Waiting",
            "OK" if review else "5",
        ),
    ]
    step_html = []
    for title, state, sub, dot in steps:
        step_html.append(
            _clean_html(
                f"""
            <div class="ha-intake-step {state}">
              <div class="ha-intake-dot">{_safe(dot)}</div>
              <div>
                <div class="ha-intake-step-title">{_safe(title)}</div>
                <div class="ha-intake-step-sub">{_safe(sub)}</div>
              </div>
            </div>
            """
            )
        )
    _render_html(f'<div class="ha-intake-stepper">{"".join(step_html)}</div>')


def _render_intake_header(record, case, docs, evidence, context, review) -> None:
    title = _intake_case_title(record, case)
    case_id = record.case_id if record else "Draft"
    updated = _format_uploaded_at(record.updated_at if record else None)
    conflict_count = (
        len(context.conflict_report.conflicts)
        if context and context.conflict_report
        else 0
    )
    missing_count = len(context.missing_information) if context else 0
    _render_html(
        f"""
        <div class="ha-card">
          <div class="ha-intake-hero">
            <div>
              <h2 class="ha-intake-title">Case Intake &amp; Assembly</h2>
              <div class="ha-intake-sub">Ingest documents, extract evidence, resolve conflicts, and assemble the case context.</div>
            </div>
            <div class="ha-intake-meta">
              <span class="ha-intake-chip">Current Case: {_safe(title)}</span>
              <span class="ha-intake-chip">Case ID: {_safe(case_id)}</span>
              <span class="ha-intake-chip">Updated: {_safe(updated)}</span>
            </div>
          </div>
        </div>
        """,
    )
    statuses = {}
    if record:
        try:
            statuses = {
                status.document_id: status
                for status in get_case_service().document_ocr_statuses(record.case_id)
            }
        except Exception:  # noqa: BLE001
            statuses = {}
    _render_intake_stepper(docs, statuses, evidence, context, review)
    if conflict_count or missing_count:
        _render_html(
            f"""
            <div class="ha-card" style="border-color:rgba(245,158,11,0.3);">
              <div style="display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap;">
                <div><span class="ha-warn" style="font-weight:850;">{conflict_count}</span> conflict(s) and <span class="ha-warn" style="font-weight:850;">{missing_count}</span> missing item(s) need attention before downstream review.</div>
                <span class="ha-pill">Evidence threshold active</span>
              </div>
            </div>
            """,
        )


def _render_document_cards(docs, statuses, evidence, context, selected_doc_id: str | None) -> None:
    evidence_counts = _doc_evidence_counts(evidence)
    conflict_counts = _doc_conflict_counts(context)
    if not docs:
        _render_html(
            """
            <div class="ha-empty">
              No documents are attached to this case yet. Use the upload controls below to start intake.
            </div>
            """,
        )
        return

    cards = []
    for doc in docs:
        status = statuses.get(doc.document_id)
        status_label, status_detail = _document_status_detail(status)
        active = "active" if doc.document_id == selected_doc_id else ""
        evidence_count = evidence_counts.get(doc.document_id, 0)
        conflict_count = conflict_counts.get(doc.document_id, 0)
        doc_type = _friendly_doc_type(doc.document_type)
        char_kb = max(doc.char_count / 1024, 0)
        cards.append(
            _clean_html(
                f"""
            <div class="ha-doc-card {active}">
              <div class="ha-doc-top">
                <div>
                  <div class="ha-doc-name">{_safe(doc.filename)}</div>
                  <div class="ha-muted" style="font-size:0.76rem;margin-top:0.25rem;">{_safe(doc_type)} * {doc.page_count} page(s) * {char_kb:.1f} KB text</div>
                </div>
                <div class="ha-doc-badges">
                  <span class="ha-pill">{_safe(doc_type)}</span>
                  <span class="ha-status-chip {_ocr_badge_class(status_label)}">{_safe(status_label)}</span>
                </div>
              </div>
              <div class="ha-doc-meta">
                <div class="ha-doc-metric">
                  <div class="ha-doc-metric-label">Evidence</div>
                  <div class="ha-doc-metric-value">{evidence_count}</div>
                </div>
                <div class="ha-doc-metric">
                  <div class="ha-doc-metric-label">Conflicts</div>
                  <div class="ha-doc-metric-value">{conflict_count}</div>
                </div>
                <div class="ha-doc-metric">
                  <div class="ha-doc-metric-label">OCR Detail</div>
                  <div class="ha-doc-metric-value">{_safe(_short(status_detail, 56))}</div>
                </div>
              </div>
            </div>
            """
            )
        )
    _render_html(f'<div class="ha-doc-list">{"".join(cards)}</div>')


def _render_intake_upload_controls(service, case_id: str, readiness) -> None:
    _render_html(
        """
        <div class="ha-doc-upload-box">
          <div style="font-weight:800;color:#f8fafc;">Add Documents</div>
          <div class="ha-muted" style="font-size:0.8rem;margin-top:0.25rem;">PDF, TXT, PNG, JPG, or JPEG documents can be ingested into the selected case.</div>
        </div>
        """,
    )
    threshold = st.slider(
        "OCR confidence threshold",
        min_value=0.0,
        max_value=1.0,
        value=float(DEFAULT_OCR_CONFIDENCE_THRESHOLD),
        step=0.05,
        key="intake_threshold",
    )
    uploads = st.file_uploader(
        "Upload documents",
        type=_INTAKE_UPLOAD_TYPES if readiness.is_available else _INTAKE_TEXT_OR_PDF_TYPES,
        accept_multiple_files=True,
        key=session.widget_key("intake_uploader"),
    )
    type_options = ["(auto-detect)"] + [category.value for category in DocumentCategory]
    override = st.selectbox(
        "Document type",
        type_options,
        key=session.widget_key("intake_doc_type"),
    )
    if not readiness.is_available:
        st.warning(readiness.message)
    elif not readiness.is_real_ocr:
        st.caption(readiness.message)

    if uploads and st.button("Ingest Document(s)", type="primary", key="intake_ingest_run"):
        override_val = None if override == "(auto-detect)" else override
        for upload in uploads:
            doc, result = service.ingest_document(
                case_id,
                upload.name,
                upload.getvalue(),
                category_override=override_val,
                ocr_confidence_threshold=threshold,
            )
            method = (
                result.ocr_results[0].processing_method.value
                if result.ocr_results
                else ("TEXT" if result.kind.value == "TEXT" else "TEXT_LAYER")
            )
            message = (
                f"Ingested {upload.name} as {result.kind.value} "
                f"(type={doc.document_type.value}, pages={result.page_count}, method={method})."
            )
            if not result.ocr_available:
                st.warning(message + " OCR unavailable; no text extracted.")
            elif result.ocr_used and result.low_confidence_pages(threshold):
                st.warning(
                    message
                    + f" Low-confidence pages: {result.low_confidence_pages(threshold)}."
                )
            else:
                st.success(message)
            for warning in result.warnings:
                st.warning(warning)
        st.rerun()


def _run_ai_text_extractor_for_case(service, case_id: str, docs) -> bool:
    """Run hosted AI extraction over all ingested document text for a case."""
    text = _combined_document_text(docs)
    if not text.strip():
        st.warning("No extracted document text is available for AI extraction yet.")
        return False

    llm_client = get_client_for_task(AITask.STRUCTURED_EXTRACTION)
    if not llm_client.is_ai:
        st.error(
            "AI text extraction is not configured. Set an AI backend API key, "
            "then rerun AI Text Extractor."
        )
        return False

    with st.spinner("Running AI text extraction across attached documents..."):
        try:
            result = MedicalExtractionAgent(llm_client=llm_client).extract(text)
        except ExtractionError as exc:
            st.error(f"AI text extraction failed: {exc}")
            return False

    service.attach_extraction(case_id, result.case)
    session.refresh_assembled_case(case_id, result.case)
    session.set_case(
        result.case,
        session.ExtractionMeta(
            attempts=result.attempts,
            backend=result.backend,
            repaired=result.repaired,
        ),
    )
    st.success(
        f"AI Text Extractor updated the structured case "
        f"(backend={result.backend}, attempts={result.attempts})."
    )
    return True


def _fact_rows_from_context(context, case) -> list[dict[str, object]]:
    preferred = [
        ("diagnosis", "Diagnosis"),
        ("requested_service", "Requested Drug"),
        ("tb_screen_result", "TB Screening"),
        ("step_therapy_status", "Step Therapy"),
        ("specialist_status", "Specialist Visit"),
        ("prior_auth_status", "Prior Auth"),
    ]
    rows: list[dict[str, object]] = []
    used = set()
    if context:
        for fact_type, label in preferred:
            fact = context.resolved_facts.get(fact_type)
            if not fact:
                continue
            rows.append(
                {
                    "label": label,
                    "value": fact.value,
                    "confidence": fact.confidence_score,
                    "source": fact.source_filename or "Evidence",
                }
            )
            used.add(fact_type)
        for fact_type, fact in context.resolved_facts.items():
            if fact_type in used or len(rows) >= 6:
                continue
            rows.append(
                {
                    "label": _friendly_fact_label(fact_type),
                    "value": fact.value,
                    "confidence": fact.confidence_score,
                    "source": fact.source_filename or "Evidence",
                }
            )
    if rows or not case:
        return rows[:6]

    fallback = [
        ("Diagnosis", case.diagnosis, case.confidence_score, "Structured case"),
        ("Requested Drug", case.requested_service, case.confidence_score, "Structured case"),
        ("Payer", case.insurance_company, case.confidence_score, "Structured case"),
        ("Decision", case.decision.value, case.confidence_score, "Structured case"),
    ]
    return [
        {
            "label": label,
            "value": value,
            "confidence": confidence,
            "source": source,
        }
        for label, value, confidence, source in fallback
        if value
    ][:6]


def _render_assembly_overview(docs, evidence, context, case, quality, decisions) -> None:
    conflict_count = (
        len(context.conflict_report.conflicts)
        if context and context.conflict_report
        else 0
    )
    missing_count = len(context.missing_information) if context else 0
    high, medium, low = _quality_counts(evidence, quality)
    _render_html(
        f"""
        <div class="ha-card">
          <h3>Assembly Overview</h3>
          <div class="ha-evidence-grid">
            <div class="ha-stat"><div class="ha-stat-value">{len(docs)}</div><div class="ha-stat-label">Documents</div></div>
            <div class="ha-stat"><div class="ha-stat-value">{len(evidence)}</div><div class="ha-stat-label">Evidence Items</div></div>
            <div class="ha-stat"><div class="ha-stat-value ha-warn">{conflict_count}</div><div class="ha-stat-label">Conflicts</div></div>
            <div class="ha-stat"><div class="ha-stat-value ha-red">{missing_count}</div><div class="ha-stat-label">Missing Criteria</div></div>
          </div>
          <div class="ha-legend" style="margin-top:0.75rem;">
            <span style="--dot:var(--ha-green);">High ({high})</span>
            <span style="--dot:var(--ha-warn);">Medium ({medium})</span>
            <span style="--dot:var(--ha-red);">Low ({low})</span>
          </div>
        </div>
        """,
    )

    fact_rows = _fact_rows_from_context(context, case)
    if not fact_rows:
        _render_html(
            """
            <div class="ha-card">
              <h3>Assembled Case Facts</h3>
              <div class="ha-empty">Assemble the case to populate source-backed facts.</div>
            </div>
            """,
        )
        return

    cards = []
    for row in fact_rows:
        confidence = float(row.get("confidence") or 0.0)
        label = "High" if confidence >= 0.8 else "Medium" if confidence >= 0.55 else "Low"
        klass = "ha-green" if label == "High" else "ha-warn" if label == "Medium" else "ha-red"
        cards.append(
            _clean_html(
                f"""
            <div class="ha-fact-card">
              <div class="ha-fact-label">{_safe(row['label'])}</div>
              <div class="ha-fact-value">{_safe(row['value'])}</div>
              <div class="ha-fact-sub">Confidence: <span class="{klass}">{label}</span></div>
              <div class="ha-fact-sub">Source: {_safe(row['source'])}</div>
            </div>
            """
            )
        )
    _render_html(
        f"""
        <div class="ha-card">
          <h3>Assembled Case Facts</h3>
          <div class="ha-fact-grid">{''.join(cards)}</div>
        </div>
        """,
    )


def _render_conflict_center(context) -> None:
    conflicts = context.conflict_report.conflicts if context and context.conflict_report else []
    if not conflicts:
        _render_html(
            """
            <div class="ha-card">
              <h3>Conflicts Requiring Review</h3>
              <div class="ha-empty">No conflicts have been detected in the assembled evidence.</div>
            </div>
            """,
        )
        return

    conflict = conflicts[0]
    value_html = "".join(
        _clean_html(f'<div class="ha-conflict-value">{_safe(value)}</div>')
        for value in conflict.values[:4]
    )
    _render_html(
        f"""
        <div class="ha-conflict-card">
          <div style="display:flex;justify-content:space-between;gap:1rem;align-items:start;">
            <div>
              <h3 style="margin:0 0 0.4rem;">Conflicts Requiring Review ({len(conflicts)})</h3>
              <div class="ha-item-title"><span class="ha-warn">!</span> {_safe(_friendly_fact_label(conflict.fact_type))}</div>
              <div class="ha-card-text">{_safe(conflict.description)}</div>
            </div>
            <span class="ha-status-chip">Severity {_safe(conflict.severity.value)}</span>
          </div>
          <div class="ha-conflict-values">{value_html}</div>
        </div>
        """,
    )
    st.button(
        "Resolve Conflict",
        key="intake_resolve_conflict",
        on_click=_set_nav,
        args=("Pending Conflicts",),
    )


def _preview_text_for_doc(doc, ocr_pages, query: str = "") -> str:
    if not doc:
        text = ""
    else:
        raw_pages = list(doc.pages())
        ocr_by_page = {page.page_number: page for page in ocr_pages}
        page_numbers = set(range(1, max(doc.page_count, len(raw_pages)) + 1))
        page_numbers.update(ocr_by_page)
        if not page_numbers:
            page_numbers = {1}

        lines = []
        for page_number in sorted(page_numbers):
            ocr_page = ocr_by_page.get(page_number)
            raw_text = (
                raw_pages[page_number - 1]
                if 0 <= page_number - 1 < len(raw_pages)
                else ""
            )
            if ocr_page and (ocr_page.raw_text or "").strip():
                heading = (
                    f"Page {page_number} "
                    f"({ocr_page.processing_method.value}, {ocr_page.confidence:.0%})"
                )
                body = ocr_page.raw_text
            else:
                heading = f"Page {page_number}"
                body = raw_text
            lines.append(f"{heading}\n{body or '[No extracted text on this page]'}")
        text = "\n\n".join(lines)
    query = query.strip().lower()
    if not query:
        return text
    filtered = [
        line for line in text.splitlines()
        if query in line.lower() or not line.strip()
    ]
    return "\n".join(filtered) or "No matching extracted text."


def _render_preview_panel(service, selected_doc, evidence, statuses) -> None:
    if not selected_doc:
        _render_html(
            """
            <div class="ha-preview-shell">
              <div class="ha-preview-head">
                <div>
                  <div class="ha-preview-title">Document Preview</div>
                  <div class="ha-preview-sub">Select or ingest a document to inspect source text and evidence.</div>
                </div>
              </div>
              <div class="ha-empty">No document selected.</div>
            </div>
            """,
        )
        return

    ocr_pages = service.ocr_results_for_document(selected_doc.document_id)
    status_label, status_detail = _document_status_detail(statuses.get(selected_doc.document_id))
    doc_evidence = [
        ref for ref in evidence if ref.source_document_id == selected_doc.document_id
    ]
    _render_html(
        f"""
        <div class="ha-preview-shell">
          <div class="ha-preview-head">
            <div>
              <div class="ha-preview-title">{_safe(selected_doc.filename)}</div>
              <div class="ha-preview-sub">{_safe(_friendly_doc_type(selected_doc.document_type))} * {selected_doc.page_count} page(s) * {len(doc_evidence)} evidence item(s)</div>
            </div>
            <span class="ha-status-chip {_ocr_badge_class(status_label)}">{_safe(status_label)}</span>
          </div>
        </div>
        """,
    )
    tab_text, tab_evidence, tab_meta, tab_ocr = st.tabs(
        ["Extracted Text", f"Evidence ({len(doc_evidence)})", "Metadata", "OCR Details"]
    )
    with tab_text:
        query = st.text_input(
            "Search extracted text",
            key=f"preview_search_{selected_doc.document_id}",
            placeholder="Search in extracted text...",
        )
        st.text_area(
            "Document text",
            value=_preview_text_for_doc(selected_doc, ocr_pages, query),
            height=360,
            key=f"preview_text_{selected_doc.document_id}",
            label_visibility="collapsed",
        )
    with tab_evidence:
        if doc_evidence:
            st.dataframe(
                [
                    {
                        "fact": ref.fact_type or "unknown",
                        "value": ref.normalized_fact.split(": ", 1)[-1],
                        "page": ref.page_number,
                        "confidence": f"{ref.confidence_score:.0%}",
                    }
                    for ref in doc_evidence
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No evidence has been assembled from this document yet.")
    with tab_meta:
        st.json(
            {
                "document_id": selected_doc.document_id,
                "type": selected_doc.document_type.value,
                "uploaded_at": selected_doc.uploaded_at,
                "pages": selected_doc.page_count,
                "characters": selected_doc.char_count,
                "ocr_status": status_label,
                "ocr_detail": status_detail,
            }
        )
    with tab_ocr:
        if ocr_pages:
            st.dataframe(
                [
                    {
                        "page": page.page_number,
                        "method": page.processing_method.value,
                        "confidence": f"{page.confidence:.0%}",
                        "characters": page.char_count,
                    }
                    for page in ocr_pages
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(status_detail)


def _assemble_case_from_intake(service, case_id: str) -> None:
    context = service.assemble_case(case_id)
    try:
        service.score_evidence(case_id)
    except Exception:  # noqa: BLE001 - quality scoring should not block assembly
        pass
    record = service.get_case(case_id)
    patient_case = (
        record.patient_case
        if record and record.patient_case is not None
        else context.patient_case
    )
    session.refresh_assembled_case(case_id, patient_case)


def _render_case_intake_assembly() -> None:
    service = get_case_service()
    _render_topbar()
    case_id = select_or_create_case(service, key_prefix="intake")
    if not case_id:
        _render_html(
            """
            <div class="ha-card">
              <h3>Case Intake & Assembly</h3>
              <div class="ha-empty">Create a case to begin document intake and assembly.</div>
            </div>
            """,
        )
        return

    record = service.get_case(case_id)
    docs = service.list_documents(case_id)
    evidence = service.list_evidence(case_id)
    quality = service.list_evidence_quality(case_id)
    decisions = service.list_evidence_decisions(case_id)
    review = record.review_result if record and record.review_result else session.get_review()
    case = record.patient_case if record and record.patient_case else session.get_case()
    statuses = {
        status.document_id: status
        for status in service.document_ocr_statuses(case_id)
    }
    context = _context_from_evidence(service, case_id, docs, evidence)
    readiness = service.ocr_readiness()

    _render_intake_header(record, case, docs, evidence, context, review)

    doc_options = [doc.document_id for doc in docs]
    selected_doc_key = session.widget_key(f"intake_preview_doc_{case_id}")
    if doc_options:
        current_selected = st.session_state.get(selected_doc_key)
        if current_selected not in doc_options:
            st.session_state[selected_doc_key] = doc_options[0]
    selected_doc_id = st.session_state.get(selected_doc_key) if doc_options else None

    left, middle, right = st.columns([0.95, 1.08, 1.28])
    with left:
        _render_html(
            f"""
            <div class="ha-card">
              <div style="display:flex;justify-content:space-between;gap:1rem;align-items:center;">
                <h3 style="margin:0;">Documents ({len(docs)})</h3>
                <span class="ha-pill">{_safe(readiness.description)}</span>
              </div>
            </div>
            """,
        )
        if doc_options:
            st.caption("Preview document")
            selected_doc_id = st.radio(
                "Preview document",
                doc_options,
                format_func=lambda doc_id: next(
                    (doc.filename for doc in docs if doc.document_id == doc_id),
                    doc_id,
                ),
                key=selected_doc_key,
                label_visibility="collapsed",
            )
        _render_document_cards(docs, statuses, evidence, context, selected_doc_id)
        _render_intake_upload_controls(service, case_id, readiness)
    selected_doc = next(
        (doc for doc in docs if doc.document_id == selected_doc_id),
        docs[0] if docs else None,
    )
    with middle:
        _render_assembly_overview(docs, evidence, context, case, quality, decisions)
        _render_conflict_center(context)
    with right:
        _render_preview_panel(service, selected_doc, evidence, statuses)

    b1, b_ai, b2, b3, b4 = st.columns([1, 1.1, 1.25, 1, 1.15])
    b1.button(
        "Reprocess OCR",
        key="intake_reprocess_ocr",
        disabled=True,
        help="Re-upload a source file to rerun OCR with the active provider.",
    )
    if b_ai.button(
        "AI Text Extractor",
        key="intake_ai_text_extractor",
        disabled=not bool(docs),
        help="Runs the configured AI backend across all attached document text.",
    ):
        if _run_ai_text_extractor_for_case(service, case_id, docs):
            st.rerun()
    if b2.button(
        "Assemble Case",
        type="primary",
        key="intake_assemble_case",
        disabled=not bool(docs),
    ):
        _assemble_case_from_intake(service, case_id)
        st.rerun()
    b3.button(
        "Clinical Review",
        key="intake_go_review",
        on_click=_set_nav,
        args=("Clinical Review",),
        disabled=not bool(evidence),
    )
    b4.button(
        "Governance Settings",
        key="intake_go_governance",
        on_click=_set_nav,
        args=("Operations & Governance",),
    )


def _render_operations_governance_page() -> None:
    _render_topbar()
    st.markdown("### Operations & Governance")
    governance_tab, payer_tab, health_tab, validation_tab = st.tabs(
        ["Governance", "Guidelines & Policies", "Operational Health", "Validation"]
    )
    with governance_tab:
        case_ui.render_governance_settings_tab()
    with payer_tab:
        case_ui.render_payer_management_tab()
    with health_tab:
        case_ui.render_operational_health_tab()
    with validation_tab:
        case_ui.render_validation_runner_tab()


def _render_reports_analytics_page() -> None:
    _render_topbar()
    st.markdown("### Reports & Analytics")
    quality_tab, review_tab, appeal_tab, audit_tab = st.tabs(
        ["Quality Analytics", "Review Explainability", "Appeal Explainability", "Audit Log"]
    )
    with quality_tab:
        case_ui.render_quality_analytics_tab()
    with review_tab:
        case_ui.render_review_explainability_tab()
    with appeal_tab:
        case_ui.render_appeal_explainability_tab()
    with audit_tab:
        case_ui.render_audit_log_tab()


def _workflow_steps(record, case, review, appeal, docs, evidence):
    has_text = _document_ready() or any((doc.raw_text or "").strip() for doc in docs)
    has_case = bool(case)
    status = record.status if record else None
    human_done = bool(record and record.review_decisions)
    exported = status is CaseStatus.APPROVED_FOR_EXPORT
    steps = [
        {
            "label": "Case Intake & Assembly",
            "state": "done" if bool(docs) or _document_ready() else "",
            "sub": f"{len(docs) or (1 if _document_ready() else 0)} docs",
        },
        {
            "label": "Structured Extraction",
            "state": "done" if has_case else "ready" if has_text else "",
            "sub": "Local + AI ready" if has_text and not has_case else "Complete" if has_case else "Waiting",
            "actions": ("Local", "AI") if has_text and not has_case else (),
        },
        ("Evidence Assembled", bool(evidence), f"{len(evidence)} evidence items" if evidence else "Waiting"),
        ("Clinical Review", bool(review), "Complete" if review else "In progress" if case else "Waiting"),
        ("Appeal Generation", bool(appeal), "Complete" if appeal else "Waiting"),
        ("Human Review", human_done, "Complete" if human_done else "Waiting"),
        ("Export", exported, "Ready" if exported else "Waiting"),
    ]
    if not review and case:
        steps[3] = (steps[3][0], False, "In progress")
    return steps


def _step_parts(step):
    if isinstance(step, dict):
        return (
            step["label"],
            step.get("state", ""),
            step.get("sub", ""),
            tuple(step.get("actions", ())),
        )
    label, done, sub = step
    return label, "done" if done else "", sub, ()


def _render_topbar() -> None:
    _render_html(
        """
        <div class="ha-topbar">
          <div class="ha-search">Search cases, patients, documents... <span style="margin-left:auto;">Ctrl K</span></div>
          <div></div>
          <div class="ha-user">
            <div style="font-size:1.1rem;">Alerts</div>
            <div class="ha-avatar">SC</div>
            <div>
              <div style="color:#f8fafc;font-weight:700;">Dr. Sarah Chen</div>
              <div class="ha-muted" style="font-size:0.76rem;">Clinical Reviewer</div>
            </div>
          </div>
        </div>
        """,
    )


def _render_case_header(record, case, review, docs, evidence, appeal) -> None:
    case_id = record.case_id if record else "Unsaved case"
    status = (
        "Pending review"
        if record and record.status is CaseStatus.PENDING_HUMAN_REVIEW
        else _status_text(record.status) if record else "Draft"
    )
    patient = case.patient_name if case else "No patient selected"
    member = case.member_id if case else None
    dob = case.date_of_birth if case else None
    service_name = (
        (review.service_name if review else None)
        or (case.requested_service if case else None)
        or "Requested service unavailable"
    )
    payer = (
        (review.payer_id if review else None)
        or (case.insurance_company if case else None)
        or "Payer unavailable"
    )
    meta = " * ".join(
        item
        for item in [
            patient,
            f"ID: {member}" if member else None,
            dob,
        ]
        if item
    )
    steps = _workflow_steps(record, case, review, appeal, docs, evidence)
    step_html = []
    for index, step in enumerate(steps, start=1):
        label, state, sub, actions = _step_parts(step)
        active = label == "Clinical Review" and case and not appeal
        if active and state != "done":
            state = "active"
        dot = "OK" if state == "done" else str(index)
        action_html = "".join(
            f'<span class="ha-mini-pill">{_safe(action)}</span>' for action in actions
        )
        step_html.append(
            _clean_html(
                f"""
            <div class="ha-step {state}">
              <div class="ha-dot">{_safe(dot)}</div>
              <div>
                <div class="ha-step-title">{_safe(label)}</div>
                <div class="ha-step-sub">{_safe(sub)}</div>
                <div class="ha-step-actions">{action_html}</div>
              </div>
            </div>
            """
            )
        )
    _render_html(
        f"""
        <div class="ha-case-header">
          <div class="ha-case-main">
            <div>
              <div class="ha-case-title">Case #{_safe(case_id)} <span class="ha-badge">{_safe(status)}</span></div>
              <div class="ha-case-meta">{_safe(meta or "Select or ingest a case to begin")}</div>
            </div>
            <div>
              <div class="ha-service-label">Service</div>
              <div class="ha-service-value">{_safe(service_name)}</div>
            </div>
            <div>
              <div class="ha-service-label">Payer</div>
              <div class="ha-payer">{_safe(payer)}</div>
            </div>
            <div></div>
          </div>
          <div class="ha-stepper">{''.join(step_html)}</div>
        </div>
        """,
    )


def _render_structured_extraction_actions(case) -> None:
    if case or not (_document_ready() or _selected_case_text().strip()):
        return
    col_local, col_ai, spacer = st.columns([1, 1, 5])
    if col_local.button(
        "Run Local Extraction",
        type="primary",
        key="cockpit_extract_local",
        help="Runs offline deterministic structured extraction.",
    ):
        _get_or_extract_case(force=True, mode="local")
        st.rerun()
    if col_ai.button(
        "Run AI Extraction",
        key="cockpit_extract_ai",
        help="Runs the configured AI backend for structured extraction.",
    ):
        _get_or_extract_case(force=True, mode="ai")
        st.rerun()


def _criteria_rows(review):
    if not review:
        return []
    rows = []
    if review.criteria_detail:
        for item in review.criteria_detail:
            status = item.status or (
                CriterionStatus.MET if item.met else CriterionStatus.NOT_MET
            )
            label = (
                "Met"
                if status is CriterionStatus.MET
                else "Missing"
                if status is CriterionStatus.NOT_MET
                else "Unknown"
            )
            rows.append(
                {
                    "criterion": item.description,
                    "status": label,
                    "evidence": len(item.supporting_evidence_ids),
                }
            )
        return rows
    for criterion in review.matched_criteria:
        rows.append({"criterion": criterion, "status": "Met", "evidence": 0})
    for criterion in review.missing_criteria:
        rows.append({"criterion": criterion, "status": "Missing", "evidence": 0})
    return rows


def _criteria_by_status(review, status_label: str):
    return [row for row in _criteria_rows(review) if row["status"] == status_label]


def _render_review_summary_card(review) -> None:
    confidence = _percent(review.confidence_score if review else 0.0)
    threshold = 85
    try:
        threshold = _percent(get_case_service().get_governance_settings().confidence_threshold)
    except Exception:  # noqa: BLE001 - display should tolerate config issues
        threshold = 85
    recommendation = review.recommendation.value.replace("_", " ") if review else "No review yet"
    reasoning = review.rationale if review else "Run a clinical review to populate the cockpit."
    guideline = review.guideline_id or "No guideline matched" if review else "Waiting"
    _render_html(
        f"""
        <div class="ha-card">
          <h3>Clinical Review Summary</h3>
          <div class="ha-summary-split">
            <div class="ha-gauge-wrap">
              <div class="ha-gauge" style="--value:{confidence}%;">
                <div class="ha-gauge-value">{confidence}%</div>
              </div>
              <div class="ha-gauge-caption">Confidence</div>
              <div class="ha-red" style="font-size:0.72rem;">Threshold {threshold}%</div>
            </div>
            <div class="ha-rec-panel">
              <div>
                <div class="ha-kicker">Recommendation</div>
                <div class="ha-rec">{_safe(recommendation)}</div>
              </div>
              <div>
                <div class="ha-kicker">Reasoning</div>
                <div class="ha-card-text">{_safe(reasoning)}</div>
              </div>
              <div>
                <div class="ha-kicker">Guideline</div>
                <div class="ha-card-text">{_safe(guideline)}</div>
              </div>
            </div>
          </div>
        </div>
        """,
    )
    c1, c2 = st.columns([1, 1])
    c1.button("View Full Review", key="cockpit_view_full_review", on_click=_set_nav, args=("Detailed Clinical Review",))
    c2.button("View Rationale", key="cockpit_view_rationale", on_click=_set_nav, args=("Review Explainability",))


def _render_criteria_table_card(review) -> None:
    rows = _criteria_rows(review)
    table_rows = []
    for row in rows[:8]:
        status_class = {
            "Met": "ha-status-met",
            "Missing": "ha-status-missing",
        }.get(row["status"], "ha-status-unknown")
        table_rows.append(
            _clean_html(
                f"""
            <tr>
              <td>{_safe(row['criterion'])}</td>
              <td class="{status_class}">{_safe(row['status'])}</td>
              <td>{_safe(row['evidence'])}</td>
            </tr>
            """
            )
        )
    if not table_rows:
        table_rows.append(
            _clean_html(
                """
            <tr><td colspan="3" class="ha-muted">No criteria have been evaluated yet.</td></tr>
            """
            )
        )
    _render_html(
        f"""
        <div class="ha-card">
          <h3>Criteria Evaluation</h3>
          <table class="ha-table">
            <thead><tr><th>Criterion</th><th>Status</th><th>Evidence</th></tr></thead>
            <tbody>{''.join(table_rows)}</tbody>
          </table>
        </div>
        """,
    )


def _render_criteria_met_card(review) -> None:
    met = _criteria_by_status(review, "Met")
    item_html = []
    for row in met[:5]:
        item_html.append(
            _clean_html(
                f"""
            <div class="ha-met-item">
              <div class="ha-item-title"><span class="ha-green">OK</span> {_safe(row['criterion'])}</div>
              <div class="ha-card-text">{_safe(row['evidence'])} supporting evidence item(s)</div>
            </div>
            """
            )
        )
    if not item_html:
        item_html.append('<div class="ha-empty">No met criteria are currently listed.</div>')
    _render_html(
        f"""
        <div class="ha-card">
          <h3>Criteria Met</h3>
          {''.join(item_html)}
        </div>
        """,
    )


def _missing_items(review):
    if not review:
        return []
    items = []
    if review.criteria_detail:
        for detail in review.criteria_detail:
            status = detail.status or (
                CriterionStatus.MET if detail.met else CriterionStatus.NOT_MET
            )
            if status is CriterionStatus.MET:
                continue
            body = detail.missing_evidence or [
                detail.reasoning or detail.note or "Documentation is required."
            ]
            items.append((detail.description, " ".join(body)))
    if not items:
        for item in review.missing_evidence or review.missing_criteria:
            items.append((item, "Documentation is required before the case can advance."))
    return items[:5]


def _render_missing_and_actions(review) -> None:
    missing = _missing_items(review)
    missing_html = []
    for title, body in missing:
        missing_html.append(
            _clean_html(
                f"""
            <div class="ha-missing-item">
              <div class="ha-item-title"><span class="ha-red">-</span> {_safe(title)}</div>
              <div class="ha-card-text">{_safe(body)}</div>
              <span class="ha-pill">View Guidance</span>
            </div>
            """
            )
        )
    if not missing_html:
        missing_html.append(
            '<div class="ha-empty">No missing criteria are currently listed.</div>'
        )

    actions = review.recommended_actions if review else []
    if not actions and missing:
        actions = [f"Provide documentation for {title.lower()}." for title, _ in missing[:3]]
    action_html = []
    for action in actions[:5]:
        action_html.append(
            _clean_html(
                f"""
            <div class="ha-action-item">
              <div class="ha-card-text"><span class="ha-green">OK</span> {_safe(action)}</div>
            </div>
            """
            )
        )
    if not action_html:
        action_html.append('<div class="ha-empty">No recommended actions yet.</div>')

    _render_html(
        f"""
        <div class="ha-card">
          <h3>What's Missing</h3>
          {''.join(missing_html)}
        </div>
        <div class="ha-card">
          <h3>Recommended Actions</h3>
          {''.join(action_html)}
        </div>
        """,
    )
    st.button("Generate Request Letter", key="cockpit_request_letter", on_click=_set_nav, args=("Appeal Generator",))


def _render_evidence_cards(evidence, quality, decisions) -> None:
    high, medium, low = _quality_counts(evidence, quality)
    total = len(evidence)
    quality_by_id = {item.evidence_id: item for item in quality}
    latest = _latest_decisions_by_evidence(decisions)
    high_width = max(high, 1) if total else 1
    med_width = max(medium, 1) if total else 1
    low_width = max(low, 1) if total else 1
    key_items = sorted(
        evidence,
        key=lambda ev: (
            quality_by_id[ev.evidence_id].overall_score
            if ev.evidence_id in quality_by_id
            else ev.confidence_score
        ),
        reverse=True,
    )[:4]
    item_html = []
    for ev in key_items:
        score = (
            quality_by_id[ev.evidence_id].overall_score
            if ev.evidence_id in quality_by_id
            else ev.confidence_score
        )
        if score >= 0.95:
            label = "High"
            klass = "ha-green"
        elif score >= 0.70:
            label = "Medium"
            klass = "ha-warn"
        else:
            label = "Low"
            klass = "ha-red"
        decision = latest.get(ev.evidence_id)
        decision_text = f" * {decision.decision.value}" if decision else ""
        title = ev.section_label or ev.fact_type or ev.source_filename or ev.evidence_id
        item_html.append(
            _clean_html(
                f"""
            <div class="ha-evidence-item">
              <div style="display:flex;justify-content:space-between;gap:0.75rem;">
                <div class="ha-item-title">{_safe(title)}</div>
                <div class="ha-muted">Page {_safe(ev.page_number)}</div>
              </div>
              <div class="ha-card-text">{_safe(_short(ev.quoted_text or ev.normalized_fact, 130))}</div>
              <div style="display:flex;justify-content:space-between;margin-top:0.45rem;">
                <span class="{klass}">{label}{_safe(decision_text)}</span>
                <span class="ha-muted">{score:.2f}</span>
              </div>
            </div>
            """
            )
        )
    if not item_html:
        item_html.append('<div class="ha-empty">No assembled evidence yet.</div>')

    _render_html(
        f"""
        <div class="ha-card">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <h3>Evidence Summary</h3>
            <span class="ha-pill">View All</span>
          </div>
          <div class="ha-evidence-grid">
            <div class="ha-stat"><div class="ha-stat-value ha-warn">{total}</div><div class="ha-stat-label">Total Evidence Items</div></div>
            <div class="ha-stat"><div class="ha-stat-value ha-green">{high}</div><div class="ha-stat-label">High Quality (&gt;=0.95)</div></div>
            <div class="ha-stat"><div class="ha-stat-value ha-warn">{medium}</div><div class="ha-stat-label">Medium Quality (0.70 - 0.94)</div></div>
            <div class="ha-stat"><div class="ha-stat-value ha-red">{low}</div><div class="ha-stat-label">Low Quality (&lt;0.70)</div></div>
          </div>
          <div class="ha-quality-bar" style="--high:{high_width}fr;--medium:{med_width}fr;--low:{low_width}fr;">
            <div></div><div></div><div></div>
          </div>
          <div class="ha-legend">
            <span style="--dot:var(--ha-green);">High ({high})</span>
            <span style="--dot:var(--ha-warn);">Medium ({medium})</span>
            <span style="--dot:var(--ha-red);">Low ({low})</span>
          </div>
        </div>
        <div class="ha-card">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <h3>Key Evidence</h3>
            <span class="ha-pill">View All</span>
          </div>
          {''.join(item_html)}
        </div>
        """,
    )
    st.button("View Intake & Evidence", key="cockpit_evidence_explorer", on_click=_set_nav, args=("Case Intake & Assembly",))


def _render_safety_footer(record, review) -> None:
    if record and record.status is CaseStatus.APPROVED_FOR_EXPORT:
        label = "READY"
        body = "Human review has approved this case for export."
        klass = "ha-green"
    elif review is None:
        label = "WAITING"
        body = "Clinical review has not been completed yet."
        klass = "ha-warn"
    else:
        gate = review.safety_gate or {}
        reasons = gate.get("reasons") or gate.get("validation_errors") or []
        if not reasons and review.missing_criteria:
            reasons = ["Required clinical criteria are still missing."]
        if not reasons:
            reasons = ["Human review is required before export."]
        label = "BLOCKED"
        body = reasons[0]
        klass = "ha-red"
    _render_html(
        f"""
        <div class="ha-footer-gate">
          <div style="font-weight:800;">Safety Gate</div>
          <div class="ha-muted">{_safe(body)}</div>
          <div>Export Status: <span class="{klass}" style="font-weight:850;">{_safe(label)}</span></div>
        </div>
        """,
    )


def _render_cockpit_empty(cases) -> None:
    _render_html(
        """
        <div class="ha-card">
          <h3>Clinical Review Cockpit</h3>
          <div class="ha-empty">
            No saved case is selected yet. Ingest documents or run structured extraction,
            then save the case to populate the live review cockpit.
          </div>
        </div>
        """,
    )
    c1, c2, c3 = st.columns(3)
    c1.button("Case Intake & Assembly", key="empty_ingestion", on_click=_set_nav, args=("Case Intake & Assembly",))
    c2.button("Structured Extraction", key="empty_structured", on_click=_set_nav, args=("Structured Extraction",))
    c3.button("Cases", key="empty_cases", on_click=_set_nav, args=("Cases",), disabled=not bool(cases))


def _render_clinical_cockpit() -> None:
    service = get_case_service()
    (
        record,
        cases,
        case,
        review,
        appeal,
        docs,
        evidence,
        quality,
        decisions,
    ) = _active_case_artifacts(service)
    _render_topbar()
    if not record and not case:
        _render_cockpit_empty(cases)
        return
    _render_case_header(record, case, review, docs, evidence, appeal)
    _render_structured_extraction_actions(case)
    if not review:
        _render_html(
            """
            <div class="ha-card">
              <h3>Clinical Review</h3>
              <div class="ha-empty">No clinical review is attached to this case yet.</div>
            </div>
            """,
        )

    c1, c2, c3 = st.columns(3)
    if c1.button("Run Local Review", type="primary", key="cockpit_run_local"):
        _get_or_run_review(force=True, mode="local")
        st.rerun()
    if c2.button("Run Gemini Review", key="cockpit_run_gemini"):
        _get_or_run_review(force=True, mode="gemini")
        st.rerun()
    c3.button("Detailed Review", key="cockpit_detailed_review", on_click=_set_nav, args=("Detailed Clinical Review",))

    left, middle, right = st.columns([1.23, 0.88, 0.98])
    with left:
        _render_review_summary_card(review)
        _render_criteria_table_card(review)
    with middle:
        _render_missing_and_actions(review)
    with right:
        _render_criteria_met_card(review)
    _render_evidence_cards(evidence, quality, decisions)
    _render_safety_footer(record, review)


def _render_sidebar_shell(service) -> str:
    cases = service.list_cases()
    current_upload_label = "Current upload / draft"
    _render_sidebar_html(
        """
        <div class="ha-brand">
          <div class="ha-logo">+</div>
          <div>
            <div class="ha-brand-title">HealthAI</div>
            <div class="ha-brand-sub">Prior Authorization Intelligence</div>
          </div>
        </div>
        """,
    )

    uploaded = st.sidebar.file_uploader(
        "Upload a document",
        type=sorted(SUPPORTED_EXTENSIONS.keys()),
        accept_multiple_files=False,
        help="Supported formats: PDF and TXT. Shared across all pages.",
        key=session.widget_key("shared_uploader"),
    )
    upload_is_new = _sync_uploaded_document(uploaded)
    case_selector_key = session.widget_key("shell_case_select")
    if cases:
        selected = session.get_persisted_case_id()
        case_ids = [case.case_id for case in cases]
        labels = [current_upload_label] + case_ids
        if upload_is_new or selected not in case_ids:
            selected = None
            session.set_persisted_case_id(None)
        st.session_state[case_selector_key] = selected or current_upload_label
        st.sidebar.selectbox(
            "Active case",
            labels,
            key=case_selector_key,
            on_change=_handle_shell_case_change,
            args=(case_selector_key, current_upload_label),
        )
    else:
        session.set_persisted_case_id(None)
        st.sidebar.caption("No saved cases yet.")

    _render_sidebar_html('<div class="ha-side-caption">Workflow</div>')
    nav_options = _nav_options()
    current = _normalize_nav(st.session_state.get(_NAV_KEY, "Case Intake & Assembly"))
    if current not in nav_options:
        current = "Case Intake & Assembly"
    nav = st.sidebar.radio(
        "Navigation",
        nav_options,
        index=nav_options.index(current),
        key=_NAV_KEY,
        label_visibility="collapsed",
    )

    _render_sidebar_html('<div class="ha-side-caption">System Status</div>')
    try:
        health = service.operational_health()
        status = "All systems operational" if health.is_healthy else "Needs attention"
    except Exception:  # noqa: BLE001 - diagnostics should not break navigation
        status = "Diagnostics unavailable"
    _render_sidebar_html(
        f"""
        <div class="ha-card" style="margin-bottom:0;">
          <div style="font-weight:750;">System Status</div>
          <div class="ha-muted" style="font-size:0.78rem;">{_safe(status)}</div>
        </div>
        """,
    )
    _render_sample_docs()
    return nav


def _render_selected_page(nav: str) -> None:
    nav = _normalize_nav(nav)
    if nav == "Case Intake & Assembly":
        _render_case_intake_assembly()
    elif nav == "Operations & Governance":
        _render_operations_governance_page()
    elif nav == "Reports & Analytics":
        _render_reports_analytics_page()
    elif nav == "Clinical Review":
        _render_clinical_cockpit()
    elif nav in {"My Review Queue", "Needs My Approval"}:
        case_ui.render_human_review_tab()
    elif nav in {"All Cases", "Cases"}:
        case_ui.render_case_management_tab()
    elif nav in {"Document Ingestion", "OCR Status", "Document Assembly", "Evidence Explorer"}:
        _render_case_intake_assembly()
    elif nav == "Reviewer Workbench":
        case_ui.render_reviewer_workbench_tab()
    elif nav == "Pending Conflicts":
        case_ui.render_conflict_resolution_tab()
    elif nav == "Reviewer Feedback":
        case_ui.render_reviewer_feedback_tab()
    elif nav == "Governance Settings":
        case_ui.render_governance_settings_tab()
    elif nav == "Audit Log":
        case_ui.render_audit_log_tab()
    elif nav == "Quality Analytics":
        case_ui.render_quality_analytics_tab()
    elif nav == "Review Explainability":
        case_ui.render_review_explainability_tab()
    elif nav == "Appeal Explainability":
        case_ui.render_appeal_explainability_tab()
    elif nav == "Guidelines & Policies":
        case_ui.render_payer_management_tab()
    elif nav == "Operational Health":
        case_ui.render_operational_health_tab()
    elif nav == "Validation Runner":
        case_ui.render_validation_runner_tab()
    elif nav == "Detailed Clinical Review":
        _render_clinical_review_tab()
    elif nav == "Appeal Generator":
        _render_appeal_tab()
    elif nav == "Structured Extraction":
        _render_structured_tab()
    elif nav == "Raw Text Extraction":
        _render_raw_text_tab()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def _render_legacy_tab_dashboard() -> None:
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
            key=session.widget_key("shared_uploader"),
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


def render_dashboard() -> None:
    """Render the designer-inspired HealthAI cockpit shell."""
    st.set_page_config(
        page_title="HealthAI - Prior Authorization",
        page_icon="H",
        layout="wide",
    )

    session.init_state()
    _inject_cockpit_css()

    service = get_case_service()
    nav = _render_sidebar_shell(service)
    _render_selected_page(nav)
