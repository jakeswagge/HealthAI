"""Tests for the deterministic local heuristic backend and the factory."""

from __future__ import annotations

import json

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
