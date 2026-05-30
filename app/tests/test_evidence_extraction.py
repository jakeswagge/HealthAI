"""Tests for the deterministic EvidenceExtractor (source attribution)."""

from __future__ import annotations

from app.evidence.extractor import EvidenceExtractor
from app.models.case_document import CaseDocument, DocumentCategory, PAGE_DELIMITER


def _doc(text: str, **kw) -> CaseDocument:
    return CaseDocument(case_id="C1", filename=kw.get("filename", "d.txt"),
                        document_type=kw.get("dt", DocumentCategory.OTHER),
                        page_count=kw.get("pages", 1), raw_text=text)


class TestSourceAttribution:
    def test_extracts_fields_with_source(self):
        doc = _doc(
            "Member Name: Harold Greene\nMember ID: WP-1\n"
            "Diagnosis: Rheumatoid arthritis\nProcedure: Humira",
            filename="denial.txt",
        )
        refs = EvidenceExtractor().extract(doc)
        facts = {r.fact_type for r in refs}
        assert "patient_name" in facts
        assert "member_id" in facts
        assert "diagnosis" in facts
        assert "requested_service" in facts
        for r in refs:
            assert r.source_filename == "denial.txt"
            assert r.source_document_id == doc.document_id
            assert r.quoted_text  # verbatim line captured

    def test_page_number_is_accurate(self):
        # Two pages joined by the form-feed delimiter.
        page1 = "Member Name: Harold Greene"
        page2 = "Diagnosis: Rheumatoid arthritis"
        doc = _doc(page1 + PAGE_DELIMITER + page2, pages=2)
        refs = EvidenceExtractor().extract(doc)
        name_ref = next(r for r in refs if r.fact_type == "patient_name")
        dx_ref = next(r for r in refs if r.fact_type == "diagnosis")
        assert name_ref.page_number == 1
        assert dx_ref.page_number == 2

    def test_normalized_fact_and_citation(self):
        doc = _doc("Diagnosis: Rheumatoid arthritis", filename="note.pdf", pages=1)
        ref = next(r for r in EvidenceExtractor().extract(doc) if r.fact_type == "diagnosis")
        assert ref.normalized_fact.startswith("diagnosis:")
        assert "note.pdf" in ref.citation()
        assert "p.1" in ref.citation()

    def test_denial_reason_extracted(self):
        doc = _doc(
            "Status: DENIED\n"
            "Reason for Denial: Step therapy not met; no DMARD trial documented.",
        )
        refs = EvidenceExtractor().extract(doc)
        assert any(r.fact_type == "decision" and "denied" in r.normalized_fact for r in refs)
        assert any(r.fact_type == "denial_reason" for r in refs)

    def test_codes_extracted(self):
        doc = _doc("ICD-10: M06.9\nCPT/HCPCS: J0135")
        refs = EvidenceExtractor().extract(doc)
        assert any(r.fact_type == "icd10_codes" and r.normalized_fact.endswith("M06.9") for r in refs)

    def test_no_fabrication_when_absent(self):
        doc = _doc("This document contains no recognizable fields.")
        refs = EvidenceExtractor().extract(doc)
        # Nothing matched -> no evidence invented.
        assert all(r.fact_type not in ("member_id", "diagnosis") for r in refs)
