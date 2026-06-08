"""Regression tests for selected CaseService cases flowing into dashboard tabs."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from app.appeals.appeal_agent import AppealAgentError
from app.cases.service import CaseService
from app.models.case_document import DocumentCategory
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import Recommendation
from app.ui import dashboard, session
from app.ui.tabs import assembly_tabs


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


def test_assembly_success_refreshes_session_and_reruns(
    fake_state,
    monkeypatch,
):
    authoritative_case = PatientCase(
        patient_name="John Smith",
        requested_service="Humira",
        decision=Decision.DENIED,
    )
    transient_case = authoritative_case.model_copy(update={"patient_name": "Transient"})
    document = SimpleNamespace(
        filename="denial.txt",
        document_type=SimpleNamespace(value=DocumentCategory.PRIOR_AUTH_FORM.value),
        page_count=1,
        char_count=128,
    )

    class FakeService:
        def __init__(self):
            self.assembled_case_id = None

        def list_documents(self, case_id):
            assert case_id == "case-1"
            return [document]

        def assemble_case(self, case_id):
            self.assembled_case_id = case_id
            return SimpleNamespace(patient_case=transient_case)

        def get_case(self, case_id):
            assert case_id == "case-1"
            return SimpleNamespace(patient_case=authoritative_case)

    class RerunCalled(Exception):
        pass

    service = FakeService()
    monkeypatch.setattr(assembly_tabs, "get_case_service", lambda: service)
    monkeypatch.setattr(
        assembly_tabs,
        "select_or_create_case",
        lambda _service, key_prefix="assembly": "case-1",
    )
    monkeypatch.setattr(assembly_tabs.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(assembly_tabs.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(assembly_tabs.st, "dataframe", lambda *args, **kwargs: None)
    monkeypatch.setattr(assembly_tabs.st, "file_uploader", lambda *args, **kwargs: None)
    monkeypatch.setattr(assembly_tabs.st, "selectbox", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        assembly_tabs.st,
        "button",
        lambda *args, **kwargs: kwargs.get("key") == "assembly_run",
    )
    monkeypatch.setattr(
        assembly_tabs.st,
        "rerun",
        lambda: (_ for _ in ()).throw(RerunCalled()),
    )

    session.set_case("OLD_CASE")
    session.set_review("STALE_REVIEW", used_ai=True)
    session.set_appeal("STALE_APPEAL", used_ai=True)

    with pytest.raises(RerunCalled):
        assembly_tabs.render_document_assembly_tab()

    assert service.assembled_case_id == "case-1"
    assert session.get_persisted_case_id() == "case-1"
    assert session.get_case() == authoritative_case
    assert session.get_review() is None
    assert session.get_appeal() is None


def test_appeal_tab_does_not_autogenerate_for_non_denied_database_case(
    fake_state,
    service,
    monkeypatch,
):
    record = service.create_case("approval.txt")
    service.attach_extraction(
        record.case_id,
        PatientCase(
            patient_name="Jane Smith",
            requested_service="Humira",
            decision=Decision.APPROVED,
        ),
    )
    session.set_persisted_case_id(record.case_id)
    messages = []

    class FakeColumn:
        def button(self, *args, **kwargs):
            return False

    monkeypatch.setattr(dashboard, "describe_active_backend", lambda: "test backend")
    monkeypatch.setattr(dashboard.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard.st,
        "info",
        lambda msg, *args, **kwargs: messages.append(msg),
    )
    monkeypatch.setattr(
        dashboard.st,
        "columns",
        lambda *args, **kwargs: [FakeColumn(), FakeColumn()],
    )
    monkeypatch.setattr(
        dashboard,
        "_get_or_generate_appeal",
        lambda *args, **kwargs: pytest.fail("appeal generation should be explicit"),
    )

    dashboard._render_appeal_tab()

    assert any("active denied case" in message for message in messages)


def test_appeal_generation_error_is_rendered_without_crashing(
    fake_state,
    monkeypatch,
):
    case = PatientCase(requested_service="Humira", decision=Decision.APPROVED)
    session.set_case(case)
    session.set_review("REVIEW", used_ai=False)
    messages = []

    class FakeSpinner:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class RaisingAgent:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, _case, _review):
            raise AppealAgentError("Appeal blocked: No active insurance denial found.")

    monkeypatch.setattr(dashboard.st, "spinner", lambda *args, **kwargs: FakeSpinner())
    monkeypatch.setattr(
        dashboard.st,
        "info",
        lambda msg, *args, **kwargs: messages.append(msg),
    )
    monkeypatch.setattr(dashboard, "AppealGenerationAgent", RaisingAgent)

    appeal, used_ai = dashboard._get_or_generate_appeal()

    assert appeal is None
    assert used_ai is False
    assert messages == ["Appeal blocked: No active insurance denial found."]
