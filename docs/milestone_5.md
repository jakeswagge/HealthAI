# Milestone 5 - Case Management, Human Review, Auditability & Operational Visibility

## Scope

Turn HealthAI from a document processor into an administrative workflow system:
case tracking, human review, audit logging, operational metrics, and exportable
case packages. All earlier functionality remains operational and unchanged.

## Architecture

New packages, keeping extraction / review / appeals separate:

```
app/
├── storage/             # SQLite connection + schema (shared)
│   └── database.py
├── cases/               # case management
│   ├── repository.py    # CaseRepository (CRUD + queries)
│   ├── transitions.py   # legal status-transition rules
│   ├── service.py       # CaseService (lifecycle + audit orchestration)
│   └── export.py        # export bundle (md/json) + ZIP
├── audit/               # audit logging
│   └── repository.py    # AuditRepository (append + query)
├── metrics/             # operational metrics
│   └── collector.py     # MetricsCollector / OperationalMetrics
├── models/
│   ├── case_record.py   # CaseRecord, CaseStatus, HumanReviewDecision, HumanDecision
│   └── audit_event.py   # AuditEvent, AuditEventType, AuditActor
└── ui/
    └── case_ui.py       # 4 Streamlit tabs + persistence bridge
```

The case layer composes the artifacts of M2/M3/M4 (`PatientCase`,
`ReviewResult`, `AppealLetter`) but does not import their agents. The UI passes
already-produced artifacts into `CaseService`.

## Database schema (SQLite, local)

`cases`
| column | type | notes |
|--------|------|-------|
| case_id | TEXT PK | |
| created_at / updated_at | TEXT | ISO-8601 |
| status | TEXT | CaseStatus value (indexed) |
| source_filename | TEXT | |
| assigned_reviewer | TEXT | |
| review_notes | TEXT | |
| processing_seconds | REAL | for avg-time metric |
| patient_case_json | TEXT | serialized PatientCase |
| review_result_json | TEXT | serialized ReviewResult |
| appeal_letter_json | TEXT | serialized AppealLetter |
| review_decisions_json | TEXT | list of HumanReviewDecision |

`audit_events` (append-only, indexed by case_id)
| column | type |
|--------|------|
| event_id TEXT PK, timestamp TEXT, case_id TEXT, event_type TEXT, actor TEXT, details TEXT |

## Workflow & status lifecycle

```
NEW → EXTRACTED → REVIEWED → APPEAL_GENERATED → PENDING_HUMAN_REVIEW
                                                      ├─ APPROVE → APPROVED_FOR_EXPORT
                                                      ├─ REJECT  → REJECTED
                                                      └─ REQUEST_CHANGES → APPEAL_GENERATED → PENDING_HUMAN_REVIEW
```

Illegal transitions raise `InvalidTransitionError`. Every significant action
records an `AuditEvent` (CASE_CREATED, DOCUMENT_UPLOADED, EXTRACTION_COMPLETED,
REVIEW_COMPLETED, APPEAL_GENERATED, HUMAN_REVIEW_COMPLETED, CASE_EXPORTED, plus
STATUS_CHANGED).

## Operational metrics

Computed on demand from SQLite (no external platform): documents_processed,
appeals_generated, human_reviews_completed, approval_rate, rejection_rate,
average_processing_time, fallback_rate (share of latest decisions that were
REQUEST_CHANGES), plus total cases and a status breakdown.

## Export package

`build_export_zip` produces a ZIP containing `case_summary.md`,
`patient_case.json`, `review_result.json`, `appeal_letter.md`, and
`audit_log.json`. Missing artifacts degrade gracefully (e.g. `"null"` or a
placeholder letter).

## Streamlit

Four new tabs: Case Management (list/open/export), Human Review
(approve/reject/request changes), Audit Log (filterable), Operational Metrics
(dashboard). A persistence bridge saves the in-session artifacts into a
`CaseRecord`; generating an appeal auto-persists the case into the human-review
queue. A single file-backed `CaseService` is cached as a Streamlit resource.

## Tests

`app/tests/test_case_management.py` (22 tests) covers SQLite repositories, case
lifecycle, status transitions (legal + illegal), audit logging, metrics
calculations, the human-review workflow (approve/reject/request-changes), and
export-package generation. All use an in-memory database.

## Known limitations / risks

- No authentication, authorization, or HIPAA controls (explicitly out of scope);
  the local SQLite DB stores PHI-like data unencrypted and is gitignored.
- `st.cache_resource` shares one `CaseService` per process; concurrent writers
  are not a design goal for this local tool.
- Metrics are point-in-time recomputations, not time-series.
- `average_processing_time` is populated only when a caller sets
  `processing_seconds`; the UI does not yet time end-to-end runs.
- No DB migrations: schema is created idempotently; column changes would need a
  manual migration.
