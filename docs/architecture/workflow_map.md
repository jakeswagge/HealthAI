# HealthAI — Workflow Map

> Documentation only. Describes the case lifecycle and the orchestration done
> by `app/cases/service.py` (`CaseService`).

## Case status lifecycle

`CaseStatus` values and the legal transitions enforced by
`app/cases/transitions.py` (`can_transition`; illegal jumps raise
`InvalidTransitionError`):

```
NEW ─────────────> EXTRACTED ─────> REVIEWED ─────> APPEAL_GENERATED
 │                    │                 │                 │
 │                    │                 │                 ▼
 │                    │                 │        PENDING_HUMAN_REVIEW
 │                    │                 │            │      │      │
 ▼                    ▼                 ▼      APPROVE│ REJECT│ REQUEST_CHANGES
REJECTED          REJECTED          REJECTED         ▼      ▼      │
 │ (reopen)                                APPROVED_FOR_EXPORT  REJECTED
 └──────────────────────────────────> APPEAL_GENERATED <───────────┘
```

- `NEW → EXTRACTED → REVIEWED → APPEAL_GENERATED → PENDING_HUMAN_REVIEW`
- From `PENDING_HUMAN_REVIEW`: `APPROVE → APPROVED_FOR_EXPORT`,
  `REJECT → REJECTED`, `REQUEST_CHANGES → APPEAL_GENERATED` (re-queues review).
- `REJECTED`/`APPROVED_FOR_EXPORT` can reopen to `APPEAL_GENERATED`.
- Every transition records a `STATUS_CHANGED` audit event.

## CaseService responsibilities

`CaseService` is the single façade the UI uses. On construction it wires (sharing
one SQLite connection): case/document/evidence repositories, audit, assembly
engine, OCR ingestion + provider, vision extractor, quality engine + repos +
workbench, resolution engine + repos, feedback repo, governance settings repo +
validated-evidence engine + compliance checker, and the analytics engine.

### Key operations (each records audit events)

| Method | Purpose | Audit event(s) |
|--------|---------|----------------|
| `create_case` | open a NEW case | `CASE_CREATED`, `DOCUMENT_UPLOADED` |
| `ingest_document` | detect type, OCR if needed, persist doc + OCR | `CASE_DOCUMENT_ADDED` (+ warnings) |
| `add_document` | attach a text document | `DOCUMENT_UPLOADED` |
| `assemble_case` | merge docs → `UnifiedCaseContext`, persist evidence, seed SYSTEM facts | `EXTRACTION_COMPLETED` |
| `score_evidence` | quality-score evidence | `EVIDENCE_QUALITY_SCORED` |
| `record_evidence_decision` | reviewer APPROVE/REJECT/FLAG | `EVIDENCE_REVIEW_DECISION` |
| `resolve_conflict` | human authoritative-value choice | `CONFLICT_RESOLVED`, `AUTHORITATIVE_FACT_UPDATED` |
| `update_governance_settings` | change org policy | `GOVERNANCE_SETTINGS_UPDATED` |
| `evidence_for_consumption` | governance-filter evidence | `VALIDATED_EVIDENCE_APPLIED` |
| `check_compliance` | run governance checks | `COMPLIANCE_CHECK_RUN` |
| `attach_review` | store `ReviewResult` | `REVIEW_COMPLETED` |
| `attach_appeal` | store `AppealLetter`, enter review queue | `APPEAL_GENERATED` |
| `record_human_review` | APPROVE/REJECT/REQUEST_CHANGES | `HUMAN_REVIEW_COMPLETED` |
| `mark_exported` | export bundle produced | `CASE_EXPORTED` |
| `record_reviewer_feedback` | structured feedback | `REVIEWER_FEEDBACK_RECORDED` |

> Note: the live `assemble_case` records `EXTRACTION_COMPLETED`; the parallel
> `AssemblyService.assemble` (not wired to the UI) records `CASE_ASSEMBLED` /
> `CONFLICT_DETECTED` instead.

## End-to-end workflow (happy path, scanned multi-document case)

```
1. create_case
2. ingest_document × N      (TXT/PDF/PNG/JPG → text-layer or OCR)
3. assemble_case            (evidence + conflicts + missing + PatientCase)
4. score_evidence           (quality assessments)
5. [reviewer] record_evidence_decision (approve/reject/flag)
6. [reviewer] resolve_conflict (authoritative facts)
7. [governance] evidence_for_consumption (draft=all / validated=approved-only)
8. attach_review            (ClinicalReviewEngine / GuidelineReviewAgent)
9. attach_appeal            (AppealGenerationAgent / builder) → PENDING_HUMAN_REVIEW
10. record_human_review     (APPROVE → APPROVED_FOR_EXPORT)
11. check_compliance        (governance violations)
12. export bundle (ZIP) + mark_exported
```

## UI tab → workflow stage

| Tab | Stage |
|-----|-------|
| Raw Text Extraction | inspect extracted text (M1) |
| Structured Extraction | `MedicalExtractionAgent` → `PatientCase` (M2) |
| Clinical Review | review engine/agent → `ReviewResult` (M3) |
| Appeal Generator | appeal agent/builder → `AppealLetter` (M4) |
| Document Ingestion / OCR Explorer | multi-doc upload + OCR (M9) |
| Document Assembly / Evidence Explorer / Conflict Review | assembly + evidence + conflicts (M6/7) |
| Evidence Quality / Reviewer Workbench | quality scoring + approve/reject/flag (M10) |
| Conflict Resolution / Reviewer Feedback | authoritative facts + feedback (M8) |
| Case Management / Human Review / Audit Log / Operational Metrics | case lifecycle + audit + metrics (M5) |
| Governance Settings / Quality Analytics | validated mode + analytics (M11) |

See `diagrams/workflow_state.puml` (state machine) and
`diagrams/end_to_end_sequence.puml` (sequence).
