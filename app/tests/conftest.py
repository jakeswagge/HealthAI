"""Shared pytest fixtures for HealthAI tests."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DOCS_DIR = PROJECT_ROOT / "data" / "sample_docs"

# Marker text we can reliably assert on across formats.
PDF_SAMPLE_TEXT = "HealthAI prior authorization sample PDF.\nLine two of the PDF."


@pytest.fixture(autouse=True)
def _use_mock_ocr_for_tests(monkeypatch):
    """Keep OCR-dependent tests deterministic without enabling mock in runtime."""
    monkeypatch.setenv("HEALTHAI_OCR_PROVIDER", "mock")


@pytest.fixture
def txt_bytes() -> bytes:
    """Raw bytes for a simple UTF-8 text document."""
    return "Prior authorization denial notice.\nMember: Test Patient".encode(
        "utf-8"
    )


@pytest.fixture
def pdf_bytes() -> bytes:
    """Generate a small in-memory PDF containing known text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), PDF_SAMPLE_TEXT)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def sample_txt_file() -> Path:
    """Path to the generated denial sample TXT document."""
    return SAMPLE_DOCS_DIR / "denial_case_01.txt"


@pytest.fixture
def tmp_txt_file(tmp_path: Path) -> Path:
    """A temporary .txt file on disk."""
    p = tmp_path / "note.txt"
    p.write_text("Approved: MRI left knee. CPT 73721.", encoding="utf-8")
    return p


@pytest.fixture
def tmp_pdf_file(tmp_path: Path, pdf_bytes: bytes) -> Path:
    """A temporary .pdf file on disk."""
    p = tmp_path / "note.pdf"
    p.write_bytes(pdf_bytes)
    return p


# --------------------------------------------------------------------------- #
# Milestone 2 fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def local_agent():
    """A MedicalExtractionAgent wired to the deterministic local backend."""
    from app.agents.medical_extraction_agent import MedicalExtractionAgent
    from app.services.local_client import LocalHeuristicClient

    return MedicalExtractionAgent(llm_client=LocalHeuristicClient())


@pytest.fixture
def sample_docs_dir() -> Path:
    """Path to the bundled sample document corpus."""
    return SAMPLE_DOCS_DIR

