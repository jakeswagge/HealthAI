"""Regression tests for selected CaseService cases flowing into dashboard tabs."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from app.appeals.appeal_agent import AppealAgentError
from app.cases.service import CaseService
from app.models.appeal_letter import AppealLetter
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.case_record import CaseRecord
from app.models.patient_case import Decision, PatientCase
from app.models.review_result import CriterionEvaluation, Recommendation, ReviewResult
import app.services.factory as factory
from app.services.llm_client import LLMClient, LLMResponse
from app.services.local_client import LocalHeuristicClient
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


class FakeSpinner:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


class ScriptedDetailsClient(LLMClient):
    name = "gemini"
    model = "gemini-test"

    def __init__(self):
        self.calls = 0

    @property
    def is_ai(self) -> bool:
        return True

    def complete(self, *, system, messages, max_tokens=1500, temperature=0.0):
        self.calls += 1
        return LLMResponse(
            text=(
                "{"
                '"patient_name":"Jane Smith",'
                '"member_id":"JS-1",'
                '"date_of_birth":null,'
                '"diagnosis":"Rheumatoid Arthritis",'
                '"icd10_codes":["M06.9"],'
                '"requested_service":"Humira",'
                '"cpt_codes":[],'
                '"insurance_company":"Aetna",'
                '"decision":"denied",'
                '"denial_reason":"Step therapy not met.",'
                '"physician_name":"Dr. Patel",'
                '"confidence_score":0.94'
                "}"
            ),
            model=self.model,
        )


class ScriptedReviewClient(LLMClient):
    name = "gemini"
    model = "gemini-review-test"

    def __init__(self):
        self.calls = 0

    @property
    def is_ai(self) -> bool:
        return True

    def complete(self, *, system, messages, max_tokens=1500, temperature=0.0):
        self.calls += 1
        return LLMResponse(
            text=(
                "{"
                '"guideline_id":"GL-HUMIRA-001",'
                '"service_name":"Humira (adalimumab)",'
                '"recommendation":"APPROVE",'
                '"matched_criteria":["Documented diagnosis of moderate-to-severe '
                'rheumatoid arthritis (or other approved indication)."],'
                '"missing_criteria":[],'
                '"missing_evidence":[],'
                '"recommended_actions":["Proceed with authorization."],'
                '"contraindications_found":[],'
                '"rationale":"Gemini found the supplied evidence sufficient.",'
                '"confidence_score":0.95,'
                '"criteria_detail":[{"id":"DX_CONFIRMED",'
                '"description":"Documented diagnosis of moderate-to-severe '
                'rheumatoid arthritis (or other approved indication).",'
                '"met":true,"status":"met","supporting_evidence_ids":[],'
                '"missing_evidence":[],"reasoning":"Diagnosis is documented.",'
                '"confidence_score":0.95,"review_backend":"gemini"}]'
                "}"
            ),
            model=self.model,
        )


@pytest.fixture
def fake_state(monkeypatch):
    for env_name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "HEALTHAI_LLM_BACKEND",
        "HEALTHAI_GEMINI_USE_VERTEXAI",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(factory, "_google_adc_available", lambda: False)
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


def test_shell_does_not_auto_select_saved_case_without_explicit_choice(
    fake_state,
    service,
):
    record = service.create_case("Patient Name Ellie Sattler.txt")

    active_case, cases = dashboard._case_for_shell(service)

    assert active_case is None
    assert [case.case_id for case in cases] == [record.case_id]
    assert session.get_persisted_case_id() is None


def test_cockpit_html_is_not_indented_as_markdown_code():
    step_html = dashboard._clean_html(
        """
        <div class="ha-step done">
          <div class="ha-dot">OK</div>
        </div>
        """
    )
    markup = dashboard._clean_html(
        f"""
        <div class="ha-stepper">{step_html}</div>
        """
    )

    assert markup.startswith('<div class="ha-stepper">')
    assert '<div class="ha-step done">' in markup
    assert all(not line.startswith("    ") for line in markup.splitlines())


def test_shell_case_callback_clears_current_upload_choice(fake_state):
    widget_key = "shell_case_select"
    fake_state[widget_key] = "Current upload / draft"
    session.set_persisted_case_id("case-6133EF117B7E")

    dashboard._handle_shell_case_change(widget_key, "Current upload / draft")

    assert session.get_persisted_case_id() is None

    fake_state[widget_key] = "case-new"
    dashboard._handle_shell_case_change(widget_key, "Current upload / draft")

    assert session.get_persisted_case_id() == "case-new"


def test_workflow_marks_structured_extraction_ready_for_uploaded_text(fake_state):
    session.set_text("Patient: Alan Grant\nRequested Service: MRI", page_count=1)

    steps = dashboard._workflow_steps(
        record=None,
        case=None,
        review=None,
        appeal=None,
        docs=[],
        evidence=[],
    )

    assert dashboard._step_parts(steps[1]) == (
        "Structured Extraction",
        "ready",
        "Local + AI ready",
        ("Local", "AI"),
    )


def test_sidebar_navigation_places_structured_extraction_first():
    nav_options = dashboard._nav_options()

    assert nav_options[0] == "Structured Extraction"
    assert nav_options[1] == "Case Intake & Assembly"
    assert "Clinical Review" in nav_options
    assert "Appeal Generator" in nav_options
    assert "Document Ingestion" not in nav_options
    assert "OCR Status" not in nav_options
    assert "Document Assembly" not in nav_options
    assert "Evidence Explorer" not in nav_options


def test_preview_text_preserves_raw_pages_when_ocr_is_partial():
    doc = CaseDocument(
        case_id="CASE-1",
        filename="clinical-note.txt",
        raw_text="Assessment on page one\fPlan on page two",
        page_count=2,
        document_type=DocumentCategory.CLINICAL_NOTE,
    )
    ocr_page = SimpleNamespace(
        page_number=1,
        processing_method=SimpleNamespace(value="TEXT_LAYER"),
        confidence=1.0,
        raw_text="Assessment on page one",
    )

    text = dashboard._preview_text_for_doc(doc, [ocr_page])

    assert "Page 1 (TEXT_LAYER, 100%)" in text
    assert "Assessment on page one" in text
    assert "Page 2\nPlan on page two" in text


def test_criteria_met_and_missing_are_split_by_status():
    review = ReviewResult(
        recommendation=Recommendation.INSUFFICIENT_INFORMATION,
        rationale="Full rationale should stay visible in the summary card.",
        criteria_detail=[
            CriterionEvaluation(
                id="MET",
                description="Conservative therapy completed.",
                met=True,
                supporting_evidence_ids=["ev-1", "ev-2"],
            ),
            CriterionEvaluation(
                id="MISSING",
                description="Red flag symptoms documented.",
                met=False,
                missing_evidence=["Submit neurologic exam findings."],
            ),
        ],
    )

    assert dashboard._criteria_by_status(review, "Met") == [
        {
            "criterion": "Conservative therapy completed.",
            "status": "Met",
            "evidence": 2,
        }
    ]
    assert dashboard._criteria_by_status(review, "Missing") == [
        {
            "criterion": "Red flag symptoms documented.",
            "status": "Missing",
            "evidence": 0,
        }
    ]


def test_persisted_ai_review_keeps_reasoning_provenance(fake_state, service):
    record = service.create_case("ai-review.txt")
    service.attach_extraction(
        record.case_id,
        PatientCase(
            diagnosis="Rheumatoid arthritis",
            requested_service="Humira",
            decision=Decision.DENIED,
        ),
    )
    review = ReviewResult(
        recommendation=Recommendation.DENY,
        rationale="AI review.",
        confidence_score=0.8,
        guideline_id="GL-HUMIRA-001",
        generated_by_ai=True,
        review_backend="gemini",
        review_model="gemini-test",
    )
    service.attach_review(record.case_id, review)
    session.set_persisted_case_id(record.case_id)

    loaded, used_ai = dashboard._get_or_run_review()

    assert loaded == review
    assert used_ai is True
    assert session.get_review_used_ai() is True


def test_forced_local_review_bypasses_configured_ai_client(
    fake_state,
    service,
    monkeypatch,
):
    record = service.create_case("local-review.txt")
    service.attach_extraction(
        record.case_id,
        PatientCase(
            diagnosis="Rheumatoid arthritis",
            requested_service="Humira",
            decision=Decision.DENIED,
            denial_reason="Step therapy not met: no methotrexate trial.",
        ),
    )
    session.set_persisted_case_id(record.case_id)
    monkeypatch.setattr(
        dashboard,
        "get_llm_client",
        lambda: pytest.fail("local review should not ask for configured AI"),
    )
    monkeypatch.setattr(
        dashboard.st,
        "spinner",
        lambda *args, **kwargs: FakeSpinner(),
    )

    review, used_ai = dashboard._get_or_run_review(force=True, mode="local")

    assert review is not None
    assert used_ai is False
    assert review.generated_by_ai is False


def test_ai_review_requires_ai_backend(fake_state, service, monkeypatch):
    record = service.create_case("ai-review-missing-key.txt")
    service.attach_extraction(
        record.case_id,
        PatientCase(
            diagnosis="Rheumatoid arthritis",
            requested_service="Humira",
            decision=Decision.DENIED,
        ),
    )
    session.set_persisted_case_id(record.case_id)
    messages = []
    monkeypatch.setattr(dashboard, "get_llm_client", lambda: LocalHeuristicClient())
    monkeypatch.setattr(
        dashboard.st,
        "error",
        lambda msg, *args, **kwargs: messages.append(msg),
    )

    review, used_ai = dashboard._get_or_run_review(force=True, mode="ai")

    assert review is None
    assert used_ai is False
    assert any("AI reasoning is not configured" in message for message in messages)


def test_compare_review_runs_local_and_gemini(
    fake_state,
    service,
    monkeypatch,
):
    record = service.create_case("compare-review.txt")
    service.attach_extraction(
        record.case_id,
        PatientCase(
            diagnosis="Rheumatoid arthritis",
            requested_service="Humira",
            decision=Decision.DENIED,
        ),
    )
    session.set_persisted_case_id(record.case_id)
    client = ScriptedReviewClient()
    monkeypatch.setattr(
        dashboard,
        "get_llm_client",
        lambda force=None: client if force == "gemini" else pytest.fail(force),
    )
    monkeypatch.setattr(
        dashboard.st,
        "spinner",
        lambda *args, **kwargs: FakeSpinner(),
    )

    review, used_ai = dashboard._get_or_run_review(force=True, mode="compare")

    assert review is not None
    assert used_ai is True
    assert client.calls == 1
    assert review.safety_gate["comparison"]["ai_recommendation"] == "APPROVE"
    assert service.get_case(record.case_id).review_result.safety_gate["comparison"]


def test_patient_details_extraction_uses_gemini_preferred_client(
    fake_state,
    monkeypatch,
):
    client = ScriptedDetailsClient()
    session.set_text(
        "Patnt: Jane Smiht\nDx: Rheumatiod Artharitis\nRequested: Humira",
        page_count=1,
    )
    monkeypatch.setattr(dashboard, "get_patient_details_client", lambda: client)
    monkeypatch.setattr(dashboard.st, "spinner", lambda *args, **kwargs: FakeSpinner())

    case = dashboard._get_or_extract_case(force=True)

    assert case.patient_name == "Jane Smith"
    assert case.diagnosis == "Rheumatoid Arthritis"
    assert case.confidence_score == 0.94
    assert session.get_extraction_meta().backend == "gemini"
    assert client.calls == 1


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


def test_appeal_workspace_renders_editor_layout(fake_state, service, monkeypatch):
    case = PatientCase(
        patient_name="James Torres",
        member_id="EAS004",
        diagnosis="Rheumatoid Arthritis",
        requested_service="Humira",
        decision=Decision.DENIED,
        denial_reason="Step therapy requirements not met.",
        insurance_company="Aetna",
    )
    review = ReviewResult(
        recommendation=Recommendation.INSUFFICIENT_INFORMATION,
        matched_criteria=["Diagnosis confirmed", "Specialist evaluation"],
        missing_criteria=["Methotrexate trial", "TB screening"],
        rationale="Additional evidence is needed.",
        confidence_score=0.57,
    )
    appeal = AppealLetter(
        appeal_id="APL-1",
        patient_name="James Torres",
        member_id="EAS004",
        insurance_company="Aetna",
        requested_service="Humira",
        original_decision="denied",
        appeal_reason="Address step therapy documentation.",
        recommended_next_steps=["Attach medication history."],
        letter_text="March 4, 2025\n\nTo the Medical Director,\n\nPlease reconsider Humira.",
        confidence_score=0.88,
        citations=["Clinical note, p.3"],
    )
    record = CaseRecord(case_id="PA-2025-07841", patient_case=case)
    html_chunks = []

    class FakeColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download_button(self, *args, **kwargs):
            return False

        def button(self, *args, **kwargs):
            return False

    class FakeExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(dashboard.st, "html", lambda html: html_chunks.append(html))
    monkeypatch.setattr(dashboard.st, "columns", lambda *args, **kwargs: [FakeColumn(), FakeColumn(), FakeColumn()])
    monkeypatch.setattr(dashboard.st, "expander", lambda *args, **kwargs: FakeExpander())
    monkeypatch.setattr(dashboard.st, "json", lambda *args, **kwargs: None)

    dashboard._render_appeal_workspace(record, case, review, appeal, used_ai=False)

    rendered = "\n".join(html_chunks)
    assert "Appeal Letter (Editable)" in rendered
    assert "Selected Supporting Evidence" in rendered
    assert "Citations in Letter" in rendered
    assert "Submit for Human Review" not in rendered
    assert "Review Summary" in rendered


def test_appeal_generation_error_is_rendered_without_crashing(
    fake_state,
    monkeypatch,
):
    case = PatientCase(requested_service="Humira", decision=Decision.APPROVED)
    session.set_case(case)
    session.set_review("REVIEW", used_ai=False)
    messages = []

    class RaisingFakeSpinner:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class RaisingAgent:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, _case, _review):
            raise AppealAgentError("Appeal blocked: No active insurance denial found.")

    monkeypatch.setattr(dashboard.st, "spinner", lambda *args, **kwargs: RaisingFakeSpinner())
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
