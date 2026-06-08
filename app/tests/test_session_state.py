"""Tests for Streamlit session-state caching behavior.

These verify the caching contract that prevents redundant LLM calls:
- extracted text, patient case, and review result are stored in session state
- a NEW document (changed signature) clears all cached derived data
- the SAME document (e.g. on a tab switch / rerun) preserves the cache
- explicit reprocess invalidates the case and review

We replace ``session.st.session_state`` with a dict-like fake so the module can
be exercised without a running Streamlit server. This mirrors how Streamlit's
session_state behaves for the dict operations we use.
"""

from __future__ import annotations

import pytest

from app.ui import session


class FakeSessionState(dict):
    """Dict subclass standing in for st.session_state.

    Streamlit's SessionState supports both attribute and item access plus
    ``in`` / ``.get``; dict already covers item access, ``in`` and ``.get``,
    which is all ``session.py`` relies on.
    """

    def __getattr__(self, name):  # pragma: no cover - convenience only
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):  # pragma: no cover - convenience only
        self[name] = value


@pytest.fixture
def fake_state(monkeypatch):
    state = FakeSessionState()
    monkeypatch.setattr(session.st, "session_state", state)
    session.init_state()
    return state


class TestSignature:
    def test_signature_stable_for_same_bytes(self):
        s1 = session.document_signature("a.txt", b"hello")
        s2 = session.document_signature("a.txt", b"hello")
        assert s1 == s2

    def test_signature_changes_with_content(self):
        s1 = session.document_signature("a.txt", b"hello")
        s2 = session.document_signature("a.txt", b"hello world")
        assert s1 != s2

    def test_signature_changes_with_name(self):
        s1 = session.document_signature("a.txt", b"hello")
        s2 = session.document_signature("b.txt", b"hello")
        assert s1 != s2


class TestInitState:
    def test_init_sets_defaults(self, fake_state):
        assert session.KEY_SIGNATURE in fake_state
        assert session.get_text() is None
        assert session.get_case() is None
        assert session.get_review() is None
        assert session.get_page_count() == 1


class TestNewDocumentClearsCache:
    def test_new_document_returns_true_and_clears(self, fake_state):
        # Establish a document with cached derived data.
        is_new = session.set_active_document("sig-1", "doc1.txt")
        assert is_new is True
        session.set_text("extracted text", page_count=2)
        session.set_case("CASE_OBJ", session.ExtractionMeta(attempts=1, backend="mock"))
        session.set_review("REVIEW_OBJ", used_ai=True)

        assert session.get_text() == "extracted text"
        assert session.get_case() == "CASE_OBJ"
        assert session.get_review() == "REVIEW_OBJ"

        # Switching to a NEW signature clears all derived data.
        is_new2 = session.set_active_document("sig-2", "doc2.txt")
        assert is_new2 is True
        assert session.get_text() is None
        assert session.get_case() is None
        assert session.get_review() is None
        assert session.get_page_count() == 1


class TestSameDocumentPreservesCache:
    def test_same_signature_returns_false_and_keeps_cache(self, fake_state):
        session.set_active_document("sig-1", "doc1.txt")
        session.set_text("text", page_count=1)
        session.set_case("CASE", session.ExtractionMeta())
        session.set_review("REVIEW", used_ai=False)

        # Re-activating the SAME signature (a rerun / tab switch) is a no-op.
        is_new = session.set_active_document("sig-1", "doc1.txt")
        assert is_new is False
        assert session.get_text() == "text"
        assert session.get_case() == "CASE"
        assert session.get_review() == "REVIEW"


class TestInvalidate:
    def test_invalidate_case_and_review(self, fake_state):
        session.set_active_document("sig-1", "doc1.txt")
        session.set_text("text", page_count=1)
        session.set_case("CASE", session.ExtractionMeta())
        session.set_review("REVIEW", used_ai=True)

        session.invalidate_case_and_review()
        # Text remains (no re-extraction of raw text needed); case+review gone.
        assert session.get_text() == "text"
        assert session.get_case() is None
        assert session.get_review() is None

    def test_invalidate_review_only(self, fake_state):
        session.set_active_document("sig-1", "doc1.txt")
        session.set_text("text", page_count=1)
        session.set_case("CASE", session.ExtractionMeta())
        session.set_review("REVIEW", used_ai=True)

        session.invalidate_review()
        assert session.get_case() == "CASE"
        assert session.get_review() is None

    def test_refresh_assembled_case_sets_case_and_clears_downstream_caches(
        self,
        fake_state,
    ):
        session.set_active_document("sig-1", "doc1.txt")
        session.set_text("text", page_count=2)
        session.set_case("OLD_CASE", session.ExtractionMeta(attempts=1))
        session.set_review("STALE_REVIEW", used_ai=True)
        session.set_appeal("STALE_APPEAL", used_ai=True)
        session.set_persisted_case_id("case-old")

        session.refresh_assembled_case("case-new", "ASSEMBLED_CASE")

        assert session.get_persisted_case_id() == "case-new"
        assert session.get_text() == "text"
        assert session.get_page_count() == 2
        assert session.get_case() == "ASSEMBLED_CASE"
        assert session.get_extraction_meta() is None
        assert session.get_review() is None
        assert session.get_review_used_ai() is False
        assert session.get_appeal() is None
        assert session.get_appeal_used_ai() is False


class TestClearDocument:
    def test_clear_resets_everything(self, fake_state):
        session.set_active_document("sig-1", "doc1.txt")
        session.set_text("text", page_count=3)
        session.clear_document()
        assert fake_state[session.KEY_SIGNATURE] is None
        assert session.get_text() is None
        assert session.get_case() is None
