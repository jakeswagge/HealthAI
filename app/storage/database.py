"""SQLite connection + schema management for HealthAI case management.

Tables:

``cases`` / ``audit_events`` (Milestone 5)
    Case records and the append-only audit trail.

``case_documents`` / ``evidence_references`` (Milestone 6/7)
    Multi-document support and source-backed evidence traceability.

Everything is local. The default database lives under ``data/healthai.db`` but
callers (and tests) may pass any path, including ``":memory:"``. Schema creation
is additive and idempotent, so existing databases upgrade transparently.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "healthai.db"


_CASES_DDL = """
CREATE TABLE IF NOT EXISTS cases (
    case_id           TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    status            TEXT NOT NULL,
    source_filename   TEXT,
    assigned_reviewer TEXT,
    review_notes      TEXT,
    processing_seconds REAL,
    patient_case_json   TEXT,
    review_result_json  TEXT,
    appeal_letter_json  TEXT,
    review_decisions_json TEXT NOT NULL DEFAULT '[]'
);
"""

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id   TEXT PRIMARY KEY,
    timestamp  TEXT NOT NULL,
    case_id    TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor      TEXT NOT NULL,
    details    TEXT
);
"""

# --- Milestone 6/7: multi-document assembly + evidence traceability --- #
_CASE_DOCUMENTS_DDL = """
CREATE TABLE IF NOT EXISTS case_documents (
    document_id   TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL,
    filename      TEXT NOT NULL,
    document_type TEXT NOT NULL,
    uploaded_at   TEXT NOT NULL,
    page_count    INTEGER NOT NULL DEFAULT 1,
    raw_text      TEXT NOT NULL DEFAULT ''
);
"""

_EVIDENCE_DDL = """
CREATE TABLE IF NOT EXISTS evidence_references (
    evidence_id        TEXT PRIMARY KEY,
    case_id            TEXT NOT NULL,
    source_document_id TEXT NOT NULL,
    source_filename    TEXT,
    page_number        INTEGER NOT NULL DEFAULT 1,
    section_label      TEXT,
    quoted_text        TEXT,
    normalized_fact    TEXT,
    fact_type          TEXT,
    confidence_score   REAL NOT NULL DEFAULT 0.0,
    created_at         TEXT NOT NULL
);
"""

# --- Milestone 8: human conflict resolution + reviewer feedback --- #
_CONFLICT_RESOLUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS conflict_resolutions (
    resolution_id   TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL,
    conflict_id     TEXT NOT NULL,
    fact_type       TEXT,
    chosen_value    TEXT NOT NULL,
    rejected_values_json TEXT NOT NULL DEFAULT '[]',
    reviewer_name   TEXT NOT NULL,
    justification   TEXT,
    timestamp       TEXT NOT NULL
);
"""

_AUTHORITATIVE_FACTS_DDL = """
CREATE TABLE IF NOT EXISTS authoritative_facts (
    fact_id           TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL,
    fact_type         TEXT NOT NULL,
    value             TEXT NOT NULL,
    source_document   TEXT,
    source_page       INTEGER,
    resolution_source TEXT NOT NULL DEFAULT 'SYSTEM',
    confidence        REAL NOT NULL DEFAULT 0.0,
    resolution_id     TEXT,
    updated_at        TEXT NOT NULL,
    UNIQUE(case_id, fact_type)
);
"""

_REVIEWER_FEEDBACK_DDL = """
CREATE TABLE IF NOT EXISTS reviewer_feedback (
    feedback_id  TEXT PRIMARY KEY,
    case_id      TEXT NOT NULL,
    reviewer     TEXT NOT NULL,
    target_type  TEXT NOT NULL,
    target_id    TEXT,
    feedback     TEXT NOT NULL,
    comments     TEXT,
    timestamp    TEXT NOT NULL
);
"""

# --- Milestone 9: OCR + intelligent ingestion --- #
_OCR_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS ocr_results (
    ocr_id            TEXT PRIMARY KEY,
    case_id           TEXT,
    document_id       TEXT NOT NULL,
    page_number       INTEGER NOT NULL DEFAULT 1,
    raw_text          TEXT,
    confidence        REAL NOT NULL DEFAULT 0.0,
    processing_method TEXT NOT NULL,
    timestamp         TEXT NOT NULL
);
"""

# --- Milestone 10: evidence quality + reviewer workbench --- #
_EVIDENCE_QUALITY_DDL = """
CREATE TABLE IF NOT EXISTS evidence_quality (
    assessment_id      TEXT PRIMARY KEY,
    evidence_id        TEXT NOT NULL,
    case_id            TEXT,
    completeness_score REAL NOT NULL DEFAULT 0.0,
    relevance_score    REAL NOT NULL DEFAULT 0.0,
    consistency_score  REAL NOT NULL DEFAULT 0.0,
    traceability_score REAL NOT NULL DEFAULT 0.0,
    overall_score      REAL NOT NULL DEFAULT 0.0,
    issues_json        TEXT NOT NULL DEFAULT '[]',
    timestamp          TEXT NOT NULL
);
"""

_EVIDENCE_REVIEW_DDL = """
CREATE TABLE IF NOT EXISTS evidence_review_decisions (
    decision_id  TEXT PRIMARY KEY,
    evidence_id  TEXT NOT NULL,
    case_id      TEXT,
    reviewer     TEXT NOT NULL,
    decision     TEXT NOT NULL,
    comments     TEXT,
    timestamp    TEXT NOT NULL
);
"""

# --- Milestone 11: governance settings (single global row) --- #
_GOVERNANCE_SETTINGS_DDL = """
CREATE TABLE IF NOT EXISTS governance_settings (
    settings_id                        TEXT PRIMARY KEY,
    validated_evidence_mode            INTEGER NOT NULL DEFAULT 0,
    allow_unreviewed_evidence          INTEGER NOT NULL DEFAULT 1,
    minimum_quality_score              REAL NOT NULL DEFAULT 0.0,
    require_conflict_resolution        INTEGER NOT NULL DEFAULT 0,
    require_human_review_before_export INTEGER NOT NULL DEFAULT 0,
    updated_at                         TEXT
);
"""

_AUDIT_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_audit_case_id ON audit_events(case_id);"
)
_CASES_STATUS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);"
)
_DOCS_CASE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_case_documents_case_id ON case_documents(case_id);"
)
_EVIDENCE_CASE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_evidence_case_id ON evidence_references(case_id);"
)
_EVIDENCE_DOC_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_evidence_doc_id ON evidence_references(source_document_id);"
)
_RESOLUTIONS_CASE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_resolutions_case_id ON conflict_resolutions(case_id);"
)
_AUTH_FACTS_CASE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_auth_facts_case_id ON authoritative_facts(case_id);"
)
_FEEDBACK_CASE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_feedback_case_id ON reviewer_feedback(case_id);"
)
_OCR_CASE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_ocr_case_id ON ocr_results(case_id);"
)
_OCR_DOC_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_ocr_doc_id ON ocr_results(document_id);"
)
_EQ_CASE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_eq_case_id ON evidence_quality(case_id);"
)
_EQ_EVIDENCE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_eq_evidence_id ON evidence_quality(evidence_id);"
)
_EVR_CASE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_evr_case_id ON evidence_review_decisions(case_id);"
)
_EVR_EVIDENCE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_evr_evidence_id ON evidence_review_decisions(evidence_id);"
)


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with sensible defaults.

    Ensures the parent directory exists (for file-based databases), enables
    foreign keys, and sets ``row_factory`` to :class:`sqlite3.Row` for
    dict-like access.
    """
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they do not already exist.

    Additive and idempotent: existing M5 tables are preserved; the M6/7 tables
    are created alongside them, so older databases upgrade transparently.
    """
    conn.execute(_CASES_DDL)
    conn.execute(_AUDIT_DDL)
    conn.execute(_CASE_DOCUMENTS_DDL)
    conn.execute(_EVIDENCE_DDL)
    conn.execute(_CONFLICT_RESOLUTIONS_DDL)
    conn.execute(_AUTHORITATIVE_FACTS_DDL)
    conn.execute(_REVIEWER_FEEDBACK_DDL)
    conn.execute(_OCR_RESULTS_DDL)
    conn.execute(_EVIDENCE_QUALITY_DDL)
    conn.execute(_EVIDENCE_REVIEW_DDL)
    conn.execute(_GOVERNANCE_SETTINGS_DDL)
    conn.execute(_AUDIT_INDEX_DDL)
    conn.execute(_CASES_STATUS_INDEX_DDL)
    conn.execute(_DOCS_CASE_INDEX_DDL)
    conn.execute(_EVIDENCE_CASE_INDEX_DDL)
    conn.execute(_EVIDENCE_DOC_INDEX_DDL)
    conn.execute(_RESOLUTIONS_CASE_INDEX_DDL)
    conn.execute(_AUTH_FACTS_CASE_INDEX_DDL)
    conn.execute(_FEEDBACK_CASE_INDEX_DDL)
    conn.execute(_OCR_CASE_INDEX_DDL)
    conn.execute(_OCR_DOC_INDEX_DDL)
    conn.execute(_EQ_CASE_INDEX_DDL)
    conn.execute(_EQ_EVIDENCE_INDEX_DDL)
    conn.execute(_EVR_CASE_INDEX_DDL)
    conn.execute(_EVR_EVIDENCE_INDEX_DDL)
    conn.commit()
