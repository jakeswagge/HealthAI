"""Tests for the deterministic local heuristic backend and the factory."""

from __future__ import annotations

import json

import pytest

from app.agents.prompts import build_extraction_messages
from app.services.factory import get_llm_client
from app.services.local_client import LocalHeuristicClient


def _run(text: str) -> dict:
    client = LocalHeuristicClient()
    messages = build_extraction_messages(text)
    resp = client.complete(system="sys", messages=messages)
    return json.loads(resp.text)


class TestLocalClientParsing:
    def test_extracts_denial_fields(self):
        text = (
            "Payer: Test Health Plan\n"
            "Member Name: Alice Example\n"
            "Member ID: TST-12345\n"
            "Date of Birth: 01/01/1980\n"
            "Diagnosis: I42.0 (Dilated cardiomyopathy)\n"
            "Procedure: Cardiac MRI with contrast\n"
            "CPT Code(s): 75561\n"
            "Status: DENIED\n"
            "Rationale: Not medically necessary per policy.\n"
            "Requesting Provider: Dr. Karen Whitfield, MD\n"
        )
        data = _run(text)
        assert data["patient_name"] == "Alice Example"
        assert data["member_id"] == "TST-12345"
        assert data["date_of_birth"] == "01/01/1980"
        assert data["decision"] == "denied"
        assert "75561" in data["cpt_codes"]
        assert "I42.0" in data["icd10_codes"]
        assert data["denial_reason"] is not None
        assert 0.0 <= data["confidence_score"] <= 1.0

    def test_approval_has_no_denial_reason(self):
        text = (
            "Insurance Company: Acme\n"
            "Patient Name: Bob Example\n"
            "Status: APPROVED\n"
            "Diagnosis: K21.9\n"
        )
        data = _run(text)
        assert data["decision"] == "approved"
        assert data["denial_reason"] is None

    def test_missing_fields_are_null(self):
        text = "Status: APPROVED\nProcedure: Some service\n"
        data = _run(text)
        assert data["patient_name"] is None
        assert data["member_id"] is None
        assert data["cpt_codes"] == []

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "Pt: D. Bowman // ID 998877 // DX=RA // REQ: Humira 40mg "
                "Hx: MTX x 14mo -> no relief. PPD/TB neg. Provider: F. Poole, MD (Rheum)",
                {
                    "patient_name": "D. Bowman",
                    "member_id": "998877",
                    "date_of_birth": None,
                    "diagnosis": "Rheumatoid Arthritis",
                    "requested_service": "Humira 40mg",
                    "decision": "pending",
                },
            ),
            (
                "*** FAX TRANSMISSION 1902-A *** PAGE 1/1 *** >>> PATIENT:: "
                "SMITH, JONATHAN. [ID: #FX112] ### DX ::: RHEUMATOID ARTHRITIS "
                "*** >> REQ MEDICATION: HUMIRA. ~~~ CLinicaL NOTes: pt failed "
                "methotrexate (12 mos). tb screen=NEGATIVE. //SIGNED// DR. K. "
                "CHANDLER, RHEUMATOLOGIST. ***END OF TX***",
                {
                    "patient_name": "SMITH, JONATHAN",
                    "member_id": "FX112",
                    "date_of_birth": None,
                    "diagnosis": "RHEUMATOID ARTHRITIS",
                    "requested_service": "HUMIRA",
                    "decision": "pending",
                },
            ),
            (
                "Patnt Name: Rachel Green DO B: 05-May-1979 Memb# MES013 "
                "Diaganosis: Rheumatiod Artharitis Drug: Humeria Notes: "
                "Methatrexat faild aftr 1 yr. Quant-TB gold neg. Dr. Geller "
                "(Rheumatolgy) apprvs.",
                {
                    "patient_name": "Rachel Green",
                    "member_id": "MES013",
                    "date_of_birth": "05-May-1979",
                    "diagnosis": "Rheumatoid Arthritis",
                    "requested_service": "Humira",
                    "decision": "pending",
                },
            ),
            (
                "Name\nMonica Geller\nID\nMES014\nDiagnosis\nRheumatoid Arthritis\n"
                "Medication\nHumira\nMethotrexate Status\nFailed\nTB Status\n"
                "Negative\nSpecialist\nRheumatologist",
                {
                    "patient_name": "Monica Geller",
                    "member_id": "MES014",
                    "date_of_birth": None,
                    "diagnosis": "Rheumatoid Arthritis",
                    "requested_service": "Humira",
                    "decision": "pending",
                },
            ),
            (
                "Patient Phoebe Buffay ID MES015 DOB 02/16/1980 presents today "
                "with severe joint inflammation requesting authorization for Humira "
                "due to primary diagnosis of rheumatoid arthritis patient has already "
                "completed a 12 month trial of methotrexate without clinical improvement "
                "tuberculosis screening was completed last week and was negative patient "
                "is established with our rheumatology department.",
                {
                    "patient_name": "Phoebe Buffay",
                    "member_id": "MES015",
                    "date_of_birth": "02/16/1980",
                    "diagnosis": "rheumatoid arthritis",
                    "requested_service": "Humira",
                    "decision": "pending",
                },
            ),
        ],
    )
    def test_messy_ocr_and_regex_stress_cases_11_to_15(self, text, expected):
        data = _run(text)

        for key, value in expected.items():
            assert data[key] == value

    def test_service_requested_alias_extracts_requested_service(self):
        data = _run(
            "Patient Name: Chandler Bing\n"
            "Member ID: MES016\n"
            "Diagnosis: Rheumatoid Arthritis\n"
            "Service Requested: Humira\n"
            "Status: Pending\n"
        )

        assert data["requested_service"] == "Humira"

    def test_educational_coverage_text_is_not_requested_service(self):
        data = _run(
            "Patient Name: Joey Tribbiani\n"
            "Member ID: MES020\n"
            "Diagnosis: Osteoarthritis\n"
            "Service Requested: is FDA-approved and covered under your plan for "
            "conditions such as Rheumatoid Arthritis and Psoriatic Arthritis. "
            "This notice is educational only.\n"
            "Status: Pending\n"
        )

        assert data["diagnosis"] == "Osteoarthritis"
        assert data["requested_service"] is None

    def test_exact_case_18_does_not_bleed_specific_diagnosis_phrase(self):
        data = _run(
            "PRIOR AUTHORIZATION PENDING / DENIAL\n"
            "Patient: Joey Tribbiani\n"
            "Member ID: PAY018\n"
            "Drug: Humira\n\n"
            "Dear Member,\n"
            "Your request for Humira has been denied due to a lack of clinical "
            "information. We received a request from your provider, but we did "
            "not receive the clinical chart notes detailing your specific "
            "diagnosis, past medication trials, or lab work (including TB "
            "screening). Without this information, we cannot determine if this "
            "medication meets medical necessity criteria."
        )

        assert data["requested_service"] == "Humira"
        assert data["diagnosis"] is None

    def test_exact_case_20_extracts_humira_and_osteoarthritis_only(self):
        data = _run(
            "PHARMACY COVERAGE DENIAL\n"
            "Patient: Janice Hosenstein\n"
            "Member ID: PAY020\n"
            "Requested: Humira\n\n"
            "Reason for decision: We cannot approve your request for Humira. "
            "This medication is FDA-approved and covered under your plan for "
            "conditions such as Rheumatoid Arthritis, Psoriatic Arthritis, and "
            "Crohn's Disease. Your provider submitted a diagnosis of "
            "Osteoarthritis. Humira is not indicated or considered medically "
            "necessary for the treatment of Osteoarthritis."
        )

        assert data["requested_service"] == "Humira"
        assert data["diagnosis"] == "Osteoarthritis"
        assert data["decision"] == "denied"


class TestFactory:
    def test_force_local_returns_local(self):
        client = get_llm_client(force="local")
        assert isinstance(client, LocalHeuristicClient)
        assert client.is_ai is False

    def test_default_without_key_is_local(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HEALTHAI_LLM_BACKEND", raising=False)
        client = get_llm_client()
        assert client.is_ai is False
