# HealthAI — Database Schema

> Documentation only. Reflects `app/storage/database.py` exactly as written.

## Engine & conventions

- **SQLite**, single local file `data/healthai.db` (tests use `":memory:"`).
- Opened via `connect()`: `row_factory = sqlite3.Row`, `PRAGMA foreign_keys = ON`.
- `initialize_schema()` runs all `CREATE TABLE IF NOT EXISTS` + index DDL — it
  is **additive and idempotent**, so older databases upgrade transparently.
- Timestamps are ISO-8601 text. Composed pydantic artifacts are stored as JSON
  text columns (e.g. `patient_case_json`).
- There are **no SQL-level foreign keys** between tables; relationships are by
  convention on `case_id` / `document_id` / `evidence_id`.

## Tables

### `cases` (M5)
| column | type | notes |
|--------|------|-------|
| case_id | TEXT PK | |
| created_at / updated_at | TEXT NOT NULL | ISO-8601 |
| status | TEXT NOT NULL | `CaseStatus` value (indexed) |
| source_filename | TEXT | |
| assigned_reviewer | TEXT | |
| review_notes | TEXT | |
| processing_seconds | REAL | |
| patient_case_json | TEXT | serialized `PatientCase` |
| review_result_json | TEXT | serialized `ReviewResult` |
| appeal_letter_json | TEXT | serialized `AppealLetter` |
| review_decisions_json | TEXT NOT NULL DEFAULT '[]' | list of `HumanReviewDecision` |

### `audit_events` (M5)
| column | type |
|--------|------|
| event_id | TEXT PK |
| timestamp | TEXT NOT NULL |
| case_id | TEXT NOT NULL (indexed) |
| event_type | TEXT NOT NULL |
| actor | TEXT NOT NULL |
| details | TEXT |

### `case_documents` (M6/7)
| column | type | notes |
|--------|------|-------|
| document_id | TEXT PK | |
| case_id | TEXT NOT NULL (indexed) | |
| filename | TEXT NOT NULL | |
| document_type | TEXT NOT NULL | `DocumentCategory` |
| uploaded_at | TEXT NOT NULL | |
| page_count | INTEGER NOT NULL DEFAULT 1 | |
| raw_text | TEXT NOT NULL DEFAULT '' | pages joined by `\f` |

### `evidence_references` (M6/7) — live evidence store
| column | type | notes |
|--------|------|-------|
| evidence_id | TEXT PK | |
| case_id | TEXT NOT NULL (indexed) | |
| source_document_id | TEXT NOT NULL (indexed) | |
| source_filename | TEXT | |
| page_number | INTEGER NOT NULL DEFAULT 1 | |
| section_label | TEXT | |
| quoted_text | TEXT | verbatim source snippet |
| normalized_fact | TEXT | `"fact_type: value"` |
| fact_type | TEXT | |
| confidence_score | REAL NOT NULL DEFAULT 0.0 | |
| created_at | TEXT NOT NULL | |

### `conflict_resolutions` (M8) — append-only
| column | type |
|--------|------|
| resolution_id | TEXT PK |
| case_id | TEXT NOT NULL (indexed) |
| conflict_id | TEXT NOT NULL |
| fact_type | TEXT |
| chosen_value | TEXT NOT NULL |
| rejected_values_json | TEXT NOT NULL DEFAULT '[]' |
| reviewer_name | TEXT NOT NULL |
| justification | TEXT |
| timestamp | TEXT NOT NULL |

### `authoritative_facts` (M8)
| column | type | notes |
|--------|------|-------|
| fact_id | TEXT PK | |
| case_id | TEXT NOT NULL (indexed) | |
| fact_type | TEXT NOT NULL | |
| value | TEXT NOT NULL | |
| source_document | TEXT | |
| source_page | INTEGER | |
| resolution_source | TEXT NOT NULL DEFAULT 'SYSTEM' | `SYSTEM` or `HUMAN` |
| confidence | REAL NOT NULL DEFAULT 0.0 | |
| resolution_id | TEXT | links to a `conflict_resolutions` row |
| updated_at | TEXT NOT NULL | |
| | UNIQUE(case_id, fact_type) | one authoritative value per fact |

### `reviewer_feedback` (M8)
| column | type |
|--------|------|
| feedback_id | TEXT PK |
| case_id | TEXT NOT NULL (indexed) |
| reviewer | TEXT NOT NULL |
| target_type | TEXT NOT NULL (EXTRACTION/REVIEW/APPEAL/ASSEMBLY) |
| target_id | TEXT |
| feedback | TEXT NOT NULL (CORRECT/INCORRECT/PARTIAL) |
| comments | TEXT |
| timestamp | TEXT NOT NULL |

### `ocr_results` (M9)
| column | type | notes |
|--------|------|-------|
| ocr_id | TEXT PK | |
| case_id | TEXT (indexed) | |
| document_id | TEXT NOT NULL (indexed) | |
| page_number | INTEGER NOT NULL DEFAULT 1 | |
| raw_text | TEXT | |
| confidence | REAL NOT NULL DEFAULT 0.0 | |
| processing_method | TEXT NOT NULL | TESSERACT/VISION_MODEL/MOCK/... |
| timestamp | TEXT NOT NULL | |

### `evidence_quality` (M10)
| column | type |
|--------|------|
| assessment_id | TEXT PK |
| evidence_id | TEXT NOT NULL (indexed) |
| case_id | TEXT (indexed) |
| completeness_score / relevance_score / consistency_score / traceability_score / overall_score | REAL NOT NULL DEFAULT 0.0 |
| issues_json | TEXT NOT NULL DEFAULT '[]' |
| timestamp | TEXT NOT NULL |

### `evidence_review_decisions` (M10) — append-only
| column | type |
|--------|------|
| decision_id | TEXT PK |
| evidence_id | TEXT NOT NULL (indexed) |
| case_id | TEXT (indexed) |
| reviewer | TEXT NOT NULL |
| decision | TEXT NOT NULL (APPROVE/REJECT/FLAG) |
| comments | TEXT |
| timestamp | TEXT NOT NULL |

### `governance_settings` (M11) — single global row
| column | type | notes |
|--------|------|-------|
| settings_id | TEXT PK | always `"GLOBAL"` |
| validated_evidence_mode | INTEGER NOT NULL DEFAULT 0 | boolean |
| allow_unreviewed_evidence | INTEGER NOT NULL DEFAULT 1 | boolean |
| minimum_quality_score | REAL NOT NULL DEFAULT 0.0 | |
| require_conflict_resolution | INTEGER NOT NULL DEFAULT 0 | boolean |
| require_human_review_before_export | INTEGER NOT NULL DEFAULT 0 | boolean |
| updated_at | TEXT | |

## Indexes

`idx_audit_case_id`, `idx_cases_status`, `idx_case_documents_case_id`,
`idx_evidence_case_id`, `idx_evidence_doc_id`, `idx_resolutions_case_id`,
`idx_auth_facts_case_id`, `idx_feedback_case_id`, `idx_ocr_case_id`,
`idx_ocr_doc_id`, `idx_eq_case_id`, `idx_eq_evidence_id`, `idx_evr_case_id`,
`idx_evr_evidence_id`.

## Logical relationships (by convention)

```
cases (case_id)
  ├──< case_documents (case_id)
  │       └──< evidence_references (source_document_id)
  ├──< evidence_references (case_id)
  │       ├──< evidence_quality (evidence_id)
  │       └──< evidence_review_decisions (evidence_id)
  ├──< ocr_results (case_id, document_id)
  ├──< conflict_resolutions (case_id) ──> authoritative_facts (resolution_id)
  ├──< authoritative_facts (case_id)
  ├──< reviewer_feedback (case_id)
  └──< audit_events (case_id)

governance_settings  # global, not per-case
```

See `diagrams/database_schema.puml` for an ER rendering.
