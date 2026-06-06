"""Session-state management for the Streamlit dashboard.

Centralizes all caching so that expensive work (text extraction and, more
importantly, Claude/LLM calls) happens only when needed:

- A document is identified by a content signature (name + size + hash). When a
  new document is uploaded, its signature changes and all cached derived data
  is cleared.
- Extracted raw text, the structured ``PatientCase``, and the ``ReviewResult``
  are stored in ``st.session_state`` keyed implicitly by the active signature.
- LLM-backed steps (extraction, review) read from the cache and only invoke the
  agent when the cache is empty or the user explicitly requests reprocessing.

Because Streamlit re-runs the whole script on every interaction (including tab
switches), caching here is what guarantees that merely switching tabs does NOT
trigger new LLM calls. See ``docs/caching.md`` for the full behavior contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Optional

import streamlit as st

# Session-state keys (namespaced to avoid collisions with widget keys).
KEY_SIGNATURE = "doc_signature"
KEY_FILENAME = "doc_filename"
KEY_TEXT = "doc_text"
KEY_PAGE_COUNT = "doc_page_count"
KEY_CASE = "patient_case"
KEY_REVIEW = "review_result"
KEY_REVIEW_USED_AI = "review_used_ai"
KEY_EXTRACTION_META = "extraction_meta"
KEY_APPEAL = "appeal_letter"
KEY_APPEAL_USED_AI = "appeal_used_ai"
KEY_PERSISTED_CASE_ID = "persisted_case_id"


def document_signature(filename: str, data: bytes) -> str:
    """Compute a stable signature for an uploaded document."""
    h = hashlib.sha256(data).hexdigest()
    return f"{filename}:{len(data)}:{h[:16]}"


@dataclass
class ExtractionMeta:
    """Lightweight metadata about the last extraction run (for display)."""

    attempts: int = 0
    backend: str = ""
    repaired: bool = False


def init_state() -> None:
    """Ensure all expected keys exist in session state."""
    defaults: dict[str, Any] = {
        KEY_SIGNATURE: None,
        KEY_FILENAME: None,
        KEY_TEXT: None,
        KEY_PAGE_COUNT: 1,
        KEY_CASE: None,
        KEY_REVIEW: None,
        KEY_REVIEW_USED_AI: False,
        KEY_EXTRACTION_META: None,
        KEY_APPEAL: None,
        KEY_APPEAL_USED_AI: False,
        KEY_PERSISTED_CASE_ID: None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _clear_derived(*, clear_persisted_case: bool = True) -> None:
    """Clear all cached data derived from a document (text, case, review)."""
    st.session_state[KEY_TEXT] = None
    st.session_state[KEY_PAGE_COUNT] = 1
    st.session_state[KEY_CASE] = None
    st.session_state[KEY_REVIEW] = None
    st.session_state[KEY_REVIEW_USED_AI] = False
    st.session_state[KEY_EXTRACTION_META] = None
    st.session_state[KEY_APPEAL] = None
    st.session_state[KEY_APPEAL_USED_AI] = False
    if clear_persisted_case:
        st.session_state[KEY_PERSISTED_CASE_ID] = None


def set_active_document(signature: str, filename: str) -> bool:
    """Mark a document as active.

    Returns True if this is a NEW document (signature changed), in which case
    all previously cached derived data is cleared. Returns False if the same
    document is still active (cache preserved) - this is the path taken on tab
    switches and unrelated reruns, ensuring no recomputation.
    """
    init_state()
    if st.session_state[KEY_SIGNATURE] != signature:
        st.session_state[KEY_SIGNATURE] = signature
        st.session_state[KEY_FILENAME] = filename
        _clear_derived()
        return True
    return False


def clear_document() -> None:
    """Reset state when no document is uploaded."""
    init_state()
    st.session_state[KEY_SIGNATURE] = None
    st.session_state[KEY_FILENAME] = None
    _clear_derived(clear_persisted_case=False)


# --------------------------------------------------------------------------- #
# Cached getters / setters
# --------------------------------------------------------------------------- #
def get_text() -> Optional[str]:
    return st.session_state.get(KEY_TEXT)


def set_text(text: str, page_count: int) -> None:
    st.session_state[KEY_TEXT] = text
    st.session_state[KEY_PAGE_COUNT] = page_count


def get_page_count() -> int:
    return st.session_state.get(KEY_PAGE_COUNT, 1)


def get_case():
    return st.session_state.get(KEY_CASE)


def set_case(case, meta: Optional[ExtractionMeta] = None) -> None:
    st.session_state[KEY_CASE] = case
    if meta is not None:
        st.session_state[KEY_EXTRACTION_META] = meta


def get_extraction_meta() -> Optional[ExtractionMeta]:
    return st.session_state.get(KEY_EXTRACTION_META)


def get_review():
    return st.session_state.get(KEY_REVIEW)


def set_review(result, used_ai: bool) -> None:
    st.session_state[KEY_REVIEW] = result
    st.session_state[KEY_REVIEW_USED_AI] = used_ai


def get_review_used_ai() -> bool:
    return st.session_state.get(KEY_REVIEW_USED_AI, False)


def get_appeal():
    return st.session_state.get(KEY_APPEAL)


def set_appeal(appeal, used_ai: bool) -> None:
    st.session_state[KEY_APPEAL] = appeal
    st.session_state[KEY_APPEAL_USED_AI] = used_ai


def get_appeal_used_ai() -> bool:
    return st.session_state.get(KEY_APPEAL_USED_AI, False)


def get_persisted_case_id() -> Optional[str]:
    return st.session_state.get(KEY_PERSISTED_CASE_ID)


def set_persisted_case_id(case_id: Optional[str]) -> None:
    st.session_state[KEY_PERSISTED_CASE_ID] = case_id


def invalidate_case_and_review() -> None:
    """Force re-extraction and re-review on next request (explicit reprocess).

    Also clears the appeal, which depends on both the case and the review.
    """
    st.session_state[KEY_CASE] = None
    st.session_state[KEY_REVIEW] = None
    st.session_state[KEY_REVIEW_USED_AI] = False
    st.session_state[KEY_EXTRACTION_META] = None
    st.session_state[KEY_APPEAL] = None
    st.session_state[KEY_APPEAL_USED_AI] = False


def invalidate_review() -> None:
    """Force re-review (and dependent appeal) on next request."""
    st.session_state[KEY_REVIEW] = None
    st.session_state[KEY_REVIEW_USED_AI] = False
    st.session_state[KEY_APPEAL] = None
    st.session_state[KEY_APPEAL_USED_AI] = False


def invalidate_appeal() -> None:
    """Force appeal regeneration on next request."""
    st.session_state[KEY_APPEAL] = None
    st.session_state[KEY_APPEAL_USED_AI] = False
