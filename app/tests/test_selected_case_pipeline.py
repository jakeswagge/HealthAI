"""Regression tests for selected CaseService cases flowing into dashboard tabs."""

from __future__ import annotations

import sqlite3

import pytest

from app.cases.service import CaseService
from app.models.case_document import DocumentCategory
from app.models.review_result import Recommendation
from app.ui import dashboard, session


class FakeSessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


@pytest.fixture
def fake_state(monkeypatch):
    state = FakeSessionState()
    monkeypatch.setattr(session.st, "session_state", state)
    session.init_state()
    return state


@pytest.fixture
def service(monkeypatch) -> CaseService:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    svc = CaseService(conn=conn)
    monkeypatch.setattr(dashboard, "get_case_service", lambda: svc)
    return svc


def test_selected_assembled_case_reaches_review_and_appeal_pipeline(
    fake_state,
    service,
):
    record = service.create_case("multi-document case")
    case_id = record.case_id
    session.set_persisted_case_id(case_id)

    service.add_document(
        case_id,
        "Patient Name John Smith2.txt",
        "Patient Name: John Smith\nMember ID: JS-123\nDiagnosis: Rheumatoid Arthritis\n",
        document_type=DocumentCategory.CLINICAL_NOTE,
    )
    service.add_document(
        case_id,
        "Patient Name John Smith3.txt",
        "Methotrexate failed after 12 months\n",
        document_type=DocumentCategory.CLINICAL_NOTE,
    )
    service.add_document(
        case_id,
        "Patient Name John Ssmiah.txt",
        "TB screen negative\nRheumatologist recommendation\nRequested Medication: Humira\nStatus: DENIED\n",
        document_type=DocumentCategory.PRIOR_AUTH_FORM,
    )

    context = service.assemble_case(case_id)
    assert context.patient_case.requested_service == "Humira"
    assert session.get_text() is None

    assert dashboard._active_pipeline_ready() is True
    case = dashboard._get_or_extract_case()
    assert case is not None
    assert case.requested_service == "Humira"
    assert session.get_case() == case

    review, used_ai = dashboard._get_or_run_review()
    assert used_ai is False
    assert review.recommendation is Recommendation.APPROVE
    assert not review.missing_criteria
    assert service.get_case(case_id).review_result == review

    appeal, used_ai = dashboard._get_or_generate_appeal()
    assert used_ai is False
    assert appeal is not None
    assert "Humira" in appeal.letter_text
    assert service.get_case(case_id).appeal_letter == appeal
