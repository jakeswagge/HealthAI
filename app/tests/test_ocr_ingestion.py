"""Tests for Milestone 9: OCR, ingestion, classification, vision evidence."""

from __future__ import annotations

import io
import json
import types
import zipfile

import pytest

from app.cases.export import build_export_files, build_export_zip
from app.cases.service import CaseService
from app.ingestion.engine import DocumentIngestionEngine, IngestionKind
from app.ingestion.classifier import DocumentClassifier
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.ocr_result import OCRPageResult, ProcessingMethod
from app.ocr.base import OCRNotAvailableError
from app.ocr.providers import (
    LocalTesseractOCRProvider,
    MockOCRProvider,
    get_ocr_provider,
    ocr_readiness,
)
from app.vision.extractor import VisionEvidenceExtractor
from app.storage.database import connect, initialize_schema


DENIAL_TEXT = (
    "Member Name: Harold Greene\nMember ID: WP-558210334\n"
    "Diagnosis: Rheumatoid arthritis\nProcedure: Humira (adalimumab)\n"
    "ICD-10: M06.9\nStatus: DENIED\n"
    "Reason for Denial: Step therapy not met; no DMARD trial documented."
)
NOTE_TEXT = "Patient: Harold Greene\nDiagnosis: Osteoarthritis"
LAB_TEXT = "Member ID: WP-558210334\nRheumatoid Factor: 85 HIGH"


@pytest.fixture
def conn():
    c = connect(":memory:")
    initialize_schema(c)
    yield c
    c.close()


@pytest.fixture
def service(conn):
    return CaseService(conn=conn)


def _png_pdf_bytes(text: str) -> bytes:
    """Mock 'image bytes': the mock OCR provider decodes bytes as text."""
    return text.encode("utf-8")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
class TestSchema:
    def test_ocr_table_exists(self, conn):
        names = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "ocr_results" in names

    def test_existing_tables_preserved(self, conn):
        names = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"cases", "audit_events", "case_documents", "evidence_references",
                "authoritative_facts", "reviewer_feedback"} <= names


# --------------------------------------------------------------------------- #
# OCR providers
# --------------------------------------------------------------------------- #
class TestOCRProviders:
    def test_mock_provider_available(self):
        p = MockOCRProvider()
        assert p.is_available is True
        assert p.processing_method is ProcessingMethod.MOCK

    def test_mock_ocr_image_one_page(self):
        p = MockOCRProvider()
        pages = p.ocr_image(_png_pdf_bytes(DENIAL_TEXT), document_id="DOC1")
        assert len(pages) == 1
        assert "Rheumatoid arthritis" in pages[0].raw_text
        assert pages[0].page_number == 1
        assert pages[0].confidence > 0

    def test_mock_ocr_multipage_pdf(self):
        p = MockOCRProvider()
        data = (DENIAL_TEXT + "\f" + NOTE_TEXT).encode("utf-8")
        pages = p.ocr_pdf(data, document_id="DOC1")
        assert len(pages) == 2
        assert pages[0].page_number == 1
        assert pages[1].page_number == 2
        assert "Osteoarthritis" in pages[1].raw_text

    def test_low_confidence_flag(self):
        p = MockOCRProvider(confidence=0.4)
        page = p.ocr_image(_png_pdf_bytes("x"), document_id="DOC1")[0]
        assert page.is_low_confidence(0.6) is True

    def test_get_provider_uses_mock_only_when_enabled(self, monkeypatch):
        monkeypatch.delenv("HEALTHAI_OCR_PROVIDER", raising=False)
        monkeypatch.delenv("HEALTHAI_ALLOW_MOCK_OCR", raising=False)
        provider = get_ocr_provider()
        assert not isinstance(provider, MockOCRProvider)

        mock = get_ocr_provider(allow_mock=True)
        assert isinstance(mock, MockOCRProvider)
        assert mock.is_available is True

    def test_readiness_marks_mock_as_not_real_ocr(self):
        readiness = ocr_readiness(MockOCRProvider())
        assert readiness.is_available is True
        assert readiness.is_real_ocr is False
        assert "not real" in readiness.message.lower()

    def test_tesseract_unavailable_raises_not_available(self):
        tess = LocalTesseractOCRProvider()
        if not tess.is_available:
            with pytest.raises(OCRNotAvailableError):
                tess.ocr_image(b"\x89PNG", document_id="DOC1")

    def test_configure_pytesseract_uses_explicit_cmd(self, monkeypatch, tmp_path):
        exe = tmp_path / "tesseract.exe"
        exe.write_text("", encoding="utf-8")
        monkeypatch.setenv("TESSERACT_CMD", str(exe))

        fake = types.SimpleNamespace(
            pytesseract=types.SimpleNamespace(tesseract_cmd="tesseract")
        )
        assert LocalTesseractOCRProvider._configure_pytesseract(fake) is True
        assert fake.pytesseract.tesseract_cmd == str(exe)


# --------------------------------------------------------------------------- #
# Ingestion detection + routing
# --------------------------------------------------------------------------- #
class TestIngestionDetection:
    def test_detect_text(self):
        eng = DocumentIngestionEngine(ocr_provider=MockOCRProvider())
        assert eng.detect_kind("note.txt", b"hello") is IngestionKind.TEXT

    def test_detect_image(self):
        eng = DocumentIngestionEngine(ocr_provider=MockOCRProvider())
        for name in ("scan.png", "fax.JPG", "img.jpeg"):
            assert eng.detect_kind(name, b"x") is IngestionKind.IMAGE

    def test_detect_unsupported(self):
        eng = DocumentIngestionEngine(ocr_provider=MockOCRProvider())
        assert eng.detect_kind("data.docx", b"x") is IngestionKind.UNSUPPORTED

    def test_searchable_vs_scanned_pdf(self):
        import fitz
        eng = DocumentIngestionEngine(ocr_provider=MockOCRProvider())

        # Searchable PDF (has a text layer).
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Member ID: WP-1 Diagnosis: Rheumatoid arthritis")
        searchable = doc.tobytes()
        doc.close()
        assert eng.detect_kind("real.pdf", searchable) is IngestionKind.SEARCHABLE_PDF

        # Scanned PDF (image-only / empty text layer).
        doc2 = fitz.open()
        doc2.new_page()
        scanned = doc2.tobytes()
        doc2.close()
        assert eng.detect_kind("scan.pdf", scanned) is IngestionKind.SCANNED_PDF


class TestIngestionRouting:
    def test_image_routes_to_ocr(self):
        eng = DocumentIngestionEngine(ocr_provider=MockOCRProvider())
        res = eng.ingest("scan.png", _png_pdf_bytes(DENIAL_TEXT), document_id="DOC1")
        assert res.kind is IngestionKind.IMAGE
        assert res.ocr_used is True
        assert res.ocr_results and res.ocr_results[0].raw_text

    def test_text_does_not_use_ocr(self):
        eng = DocumentIngestionEngine(ocr_provider=MockOCRProvider())
        res = eng.ingest("note.txt", DENIAL_TEXT.encode(), document_id="DOC1")
        assert res.ocr_used is False
        assert res.ocr_results == []

    def test_searchable_pdf_uses_text_layer(self):
        import fitz
        eng = DocumentIngestionEngine(ocr_provider=MockOCRProvider())
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Diagnosis: Rheumatoid arthritis")
        data = doc.tobytes()
        doc.close()
        res = eng.ingest("real.pdf", data, document_id="DOC1")
        assert res.kind is IngestionKind.SEARCHABLE_PDF
        assert res.ocr_used is False
        assert "Rheumatoid arthritis" in res.full_text

    def test_unsupported_warns_no_crash(self):
        eng = DocumentIngestionEngine(ocr_provider=MockOCRProvider())
        res = eng.ingest("data.docx", b"x", document_id="DOC1")
        assert res.kind is IngestionKind.UNSUPPORTED
        assert res.warnings

    def test_ocr_unavailable_degrades_gracefully(self):
        # Force an unavailable provider.
        class _Down(MockOCRProvider):
            @property
            def is_available(self):
                return False

            def ocr_image(self, *a, **k):
                raise OCRNotAvailableError("down")

            def ocr_pdf(self, *a, **k):
                raise OCRNotAvailableError("down")

        eng = DocumentIngestionEngine(ocr_provider=_Down())
        res = eng.ingest("scan.png", b"x", document_id="DOC1")
        assert res.ocr_available is False
        assert res.warnings
        assert res.pages == [""]  # no fabricated text


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
class TestClassification:
    def test_auto_classify_by_filename(self):
        c = DocumentClassifier()
        assert c.classify("denial_letter.png", "") is DocumentCategory.DENIAL_LETTER
        assert c.classify("lab_results.jpg", "") is DocumentCategory.LAB_RESULT

    def test_auto_classify_by_content(self):
        c = DocumentClassifier()
        assert c.classify("scan.png", "MRI of the lumbar spine impression") is DocumentCategory.IMAGING_REPORT

    def test_manual_override(self):
        c = DocumentClassifier()
        got = c.classify("denial_letter.png", "", override=DocumentCategory.REFERRAL)
        assert got is DocumentCategory.REFERRAL

    def test_override_string(self):
        c = DocumentClassifier()
        assert c.classify("x.png", "", override="CLINICAL_NOTE") is DocumentCategory.CLINICAL_NOTE


# --------------------------------------------------------------------------- #
# Vision evidence extraction (traceability)
# --------------------------------------------------------------------------- #
class TestVisionEvidence:
    def test_extracts_with_source_and_page(self):
        doc = CaseDocument(case_id="C1", filename="scan.png",
                           document_type=DocumentCategory.DENIAL_LETTER,
                           raw_text=DENIAL_TEXT)
        pages = [OCRPageResult(document_id=doc.document_id, case_id="C1",
                               page_number=1, raw_text=DENIAL_TEXT, confidence=0.9,
                               processing_method=ProcessingMethod.MOCK)]
        refs = VisionEvidenceExtractor().extract(doc, pages)
        assert refs
        for r in refs:
            assert r.source_document_id == doc.document_id
            assert r.source_filename == "scan.png"
            assert r.page_number == 1
            assert r.quoted_text

    def test_ocr_confidence_blended(self):
        doc = CaseDocument(case_id="C1", filename="scan.png", raw_text=DENIAL_TEXT)
        pages = [OCRPageResult(document_id=doc.document_id, page_number=1,
                               raw_text=DENIAL_TEXT, confidence=0.5,
                               processing_method=ProcessingMethod.MOCK)]
        refs = VisionEvidenceExtractor().extract(doc, pages)
        # Field confidence (0.9/0.85/0.8) * 0.5 OCR -> all <= 0.45.
        assert all(r.confidence_score <= 0.46 for r in refs)

    def test_empty_page_yields_no_evidence(self):
        doc = CaseDocument(case_id="C1", filename="blank.png", raw_text="")
        pages = [OCRPageResult(document_id=doc.document_id, page_number=1,
                               raw_text="   ", confidence=0.9)]
        assert VisionEvidenceExtractor().extract(doc, pages) == []


# --------------------------------------------------------------------------- #
# Service ingestion + persistence + audit
# --------------------------------------------------------------------------- #
class TestServiceIngestion:
    def test_ingest_image_persists_and_audits(self, service):
        rec = service.create_case("ocr case")
        doc, res = service.ingest_document(
            rec.case_id, "scanned_denial.png", _png_pdf_bytes(DENIAL_TEXT)
        )
        assert res.ocr_used is True
        assert doc.document_type is DocumentCategory.DENIAL_LETTER
        assert service.ocr_results.count_for_case(rec.case_id) == 1
        from app.models.audit_event import AuditEventType
        types = [e.event_type for e in service.history(rec.case_id)]
        assert AuditEventType.CASE_DOCUMENT_ADDED in types

    def test_low_confidence_audited(self, service):
        service.ingestion.ocr = MockOCRProvider(confidence=0.3)
        rec = service.create_case("low conf")
        service.ingest_document(rec.case_id, "scan.png", _png_pdf_bytes(DENIAL_TEXT))
        details = " ".join(e.details for e in service.history(rec.case_id)).lower()
        assert "low-confidence ocr" in details

    def test_multipage_pdf_pages_stored(self, service):
        service.ingestion.ocr = MockOCRProvider()
        rec = service.create_case("multi")
        data = (DENIAL_TEXT + "\f" + NOTE_TEXT).encode("utf-8")
        doc, res = service.ingest_document(rec.case_id, "scan.pdf", data)
        # detect_kind sees no text layer (bytes are not a real PDF) -> SCANNED.
        assert res.ocr_used is True
        assert service.ocr_results.count_for_case(rec.case_id) == res.page_count

    def test_document_status_explains_txt_skips_ocr(self, service):
        rec = service.create_case("txt")
        service.ingest_document(rec.case_id, "note.txt", DENIAL_TEXT.encode())

        status = service.document_ocr_statuses(rec.case_id)[0]
        assert status.status == "TXT files do not use OCR"
        assert status.processing_method == "TEXT"
        assert status.ocr_pages == 0

    def test_document_status_explains_searchable_pdf_text_layer(self, service):
        import fitz

        rec = service.create_case("pdf")
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Diagnosis: Rheumatoid arthritis")
        data = pdf.tobytes()
        pdf.close()

        service.ingest_document(rec.case_id, "searchable.pdf", data)
        status = service.document_ocr_statuses(rec.case_id)[0]
        assert status.status == "Text layer used"
        assert status.processing_method == "TEXT_LAYER"
        assert status.ocr_pages == 0

    def test_document_status_explains_unavailable_real_ocr(self, service):
        tess = LocalTesseractOCRProvider()
        tess._checked = False
        service.ingestion.ocr = tess

        rec = service.create_case("scan")
        service.ingest_document(rec.case_id, "scan.png", b"image bytes")

        status = service.document_ocr_statuses(rec.case_id)[0]
        assert status.status == "OCR unavailable"
        assert "Tesseract" in status.detail
        assert status.ocr_pages == 0


# --------------------------------------------------------------------------- #
# Success criterion: 3 scanned docs -> assemble -> conflict -> review/appeal
# --------------------------------------------------------------------------- #
class TestScannedWorkflow:
    def test_three_scanned_docs_full_pipeline(self, service):
        from app.review.review_agent import GuidelineReviewAgent
        from app.appeals.appeal_agent import AppealGenerationAgent
        from app.services.local_client import LocalHeuristicClient

        rec = service.create_case("scanned multi-doc")
        service.ingest_document(rec.case_id, "scanned_denial.png", _png_pdf_bytes(DENIAL_TEXT))
        service.ingest_document(rec.case_id, "clinical_note.jpg", _png_pdf_bytes(NOTE_TEXT))
        service.ingest_document(rec.case_id, "lab_report.jpeg", _png_pdf_bytes(LAB_TEXT))

        ctx = service.assemble_case(rec.case_id)
        # OCR-derived evidence assembled + a diagnosis conflict detected.
        assert ctx.evidence
        dx_conflict = [c for c in ctx.conflict_report.conflicts if c.fact_type == "diagnosis"]
        assert dx_conflict

        review = GuidelineReviewAgent(llm_client=LocalHeuristicClient()).review(ctx.patient_case).result
        service.attach_review(rec.case_id, review)
        appeal = AppealGenerationAgent(llm_client=LocalHeuristicClient()).generate(ctx.patient_case, review).appeal
        service.attach_appeal(rec.case_id, appeal)
        assert appeal.has_letter

    def test_ocr_evidence_traceable_to_source(self, service):
        rec = service.create_case("trace")
        doc, _ = service.ingest_document(rec.case_id, "scanned_denial.png", _png_pdf_bytes(DENIAL_TEXT))
        ctx = service.assemble_case(rec.case_id)
        for ev in ctx.evidence:
            assert ev.source_document_id
            assert ev.source_filename
            assert ev.page_number >= 1


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
class TestExport:
    def _ocr_case(self, service):
        rec = service.create_case("export case")
        service.ingest_document(rec.case_id, "scanned_denial.png", _png_pdf_bytes(DENIAL_TEXT))
        service.assemble_case(rec.case_id)
        return rec.case_id

    def test_export_includes_ocr_files(self, service):
        case_id = self._ocr_case(service)
        record = service.get_case(case_id)
        files = build_export_files(
            record, service.history(case_id),
            ocr_results=service.list_ocr_results(case_id),
        )
        assert "ocr_results.json" in files
        assert "document_classification.json" in files
        assert "ocr_traceability_report.md" in files
        ocr = json.loads(files["ocr_results.json"])
        assert ocr and "processing_method" in ocr[0]

    def test_export_zip_contains_ocr_files(self, service):
        case_id = self._ocr_case(service)
        record = service.get_case(case_id)
        data = build_export_zip(
            record, service.history(case_id),
            ocr_results=service.list_ocr_results(case_id),
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
        assert {"ocr_results.json", "document_classification.json",
                "ocr_traceability_report.md"} <= names

    def test_backward_compatible_export(self, service):
        rec = service.create_case("plain")
        record = service.get_case(rec.case_id)
        files = build_export_files(record, service.history(rec.case_id))
        assert "ocr_results.json" not in files
        assert set(files) == {
            "case_summary.md", "patient_case.json", "review_result.json",
            "appeal_letter.md", "audit_log.json",
        }
