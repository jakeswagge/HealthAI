# HealthAI — Package Map

> Documentation only. Reflects the current package layout under `app/`.

## Legend

- **Live**: imported (directly or transitively) by `CaseService` and/or the UI.
- **Parallel**: present in the tree but not wired into the live service/UI.

## Packages

### `app/ui` — Streamlit presentation (Live)
| Module | Responsibility |
|--------|----------------|
| `dashboard.py` | Builds the 19-tab dashboard; M1–M4 tabs render inline, M5–M11 tabs delegate to `case_ui`. Entry: `render_dashboard()`. |
| `case_ui.py` | Renders the case-management / governance tabs; holds the cached `CaseService` (`@st.cache_resource`). |
| `session.py` | `st.session_state` caching of text/case/review/appeal keyed by a document content signature (prevents redundant LLM calls). |

### `app/models` — Pydantic contracts (Live)
| Module | Key types |
|--------|-----------|
| `document.py` | `DocumentType`, `ExtractedDocument`, `SUPPORTED_EXTENSIONS` |
| `patient_case.py` | `PatientCase`, `Decision`, `FieldSource` |
| `clinical_guideline.py` | `ClinicalGuideline`, `GuidelineCriterion`, `Contraindication` |
| `review_result.py` | `ReviewResult`, `Recommendation`, `CriterionEvaluation` |
| `appeal_letter.py` | `AppealLetter` |
| `case_record.py` | `CaseRecord`, `CaseStatus`, `HumanReviewDecision`, `HumanDecision` |
| `audit_event.py` | `AuditEvent`, `AuditEventType`, `AuditActor` |
| `case_document.py` | `CaseDocument`, `DocumentCategory`, `classify_document` |
| `evidence_reference.py` | `EvidenceReference` (**live** evidence model) |
| `conflict_report.py` | `ConflictReport`, `FactConflict`, `ConflictSeverity` |
| `unified_case_context.py` | `UnifiedCaseContext`, `ResolvedFact` |
| `conflict_resolution.py` | `ConflictResolution`, `AuthoritativeFact`, `ResolutionSource` |
| `reviewer_feedback.py` | `ReviewerFeedback`, `FeedbackTarget`, `FeedbackVerdict` |
| `ocr_result.py` | `OCRPageResult`, `ProcessingMethod` |
| `evidence_quality.py` | `EvidenceQualityAssessment` |
| `evidence_review_decision.py` | `EvidenceReviewDecision`, `EvidenceDecision` |
| `governance.py` | `GovernanceSettings`, `ApprovedEvidenceSet`, `GovernanceComplianceReport`, `EvidenceMode` |
| `evidence.py` | `EvidenceReference`, `ConflictReport`, `FieldConflict` (**Parallel** model lineage) |

### `app/services` — LLM service layer (Live)
`llm_client.py` (interface `LLMClient` + `LLMResponse`/`LLMError`),
`anthropic_client.py` (Claude), `local_client.py` (regex heuristic),
`mock_claude_client.py` (scenario-driven test double), `factory.py`
(`get_llm_client`, `describe_active_backend`).

### `app/extraction` — Raw text + size (Live)
`extractor.py` (TXT/PDF text, page-aware helpers), `validation.py`
(extension/type validation), `size_validator.py` (`DocumentSizeValidator`).

### `app/agents` — Structured extraction (Live)
`medical_extraction_agent.py` (`MedicalExtractionAgent`), `prompts.py`,
`evaluation.py` (extraction eval harness).

### `app/guidelines` — Guideline library (Live)
`repository.py` (`GuidelineRepository`, loads JSON from `data/guidelines/`,
matches a `PatientCase` to a guideline).

### `app/review` — Clinical review (Live)
`engine.py` (`ClinicalReviewEngine`, deterministic), `review_agent.py`
(`GuidelineReviewAgent`, Claude + fallback), `review_prompts.py`,
`evaluation.py`.

### `app/appeals` — Appeal generation (Live)
`appeal_agent.py` (`AppealGenerationAgent`), `builder.py`
(`AppealLetterBuilder`, deterministic), `appeal_prompts.py`.

### `app/cases` — Case management + orchestration (Live)
| Module | Responsibility | Status |
|--------|----------------|--------|
| `service.py` | `CaseService` — central façade wiring all repos + engines + audit | Live |
| `repository.py` | `CaseRepository` (CRUD for `CaseRecord`) | Live |
| `document_repository.py` | `CaseDocumentRepository` | Live |
| `transitions.py` | status-transition rules (`can_transition`) | Live |
| `export.py` | `build_export_files` / `build_export_zip` (bundle) | Live |
| `assembly_service.py` | `AssemblyService` (alt orchestrator) | **Parallel** |
| `evidence_repository.py` | `EvidenceRepository` over `models.evidence` | **Parallel** |

> The cases package re-exports the **live** `EvidenceRepository` from
> `app.evidence.repository` (not `app.cases.evidence_repository`).

### `app/audit` — Audit trail (Live)
`repository.py` (`AuditRepository`, append + query of `audit_events`).

### `app/metrics` — Operational metrics (Live)
`collector.py` (`MetricsCollector`, `OperationalMetrics`).

### `app/evidence` — Deterministic evidence (Live)
`extractor.py` (`EvidenceExtractor`, regex), `linker.py` (`link_review`,
`link_appeal`), `repository.py` (`EvidenceRepository`, live).

### `app/assembly` — Multi-doc assembly (Live + Parallel)
`engine.py` (`CaseAssemblyEngine` — **Live**), `traceability.py`
(`annotate_review`/`annotate_appeal` — **Parallel**).

### `app/resolution` — Conflict resolution (Live)
`engine.py` (`ConflictResolutionEngine`), `repository.py`
(`ConflictResolutionRepository`, `AuthoritativeFactRepository`).

### `app/feedback` — Reviewer feedback (Live)
`repository.py` (`ReviewerFeedbackRepository`), `dataset.py`
(`FeedbackDataset`, learning-data export only).

### `app/ocr` — OCR (Live)
`base.py` (interface + `OCRResult` + `PlaceholderOCREngine` types),
`providers.py` (`LocalTesseractOCRProvider`, `MockOCRProvider`,
`get_ocr_provider`), `repository.py` (`OCRResultRepository`), `factory.py`,
`placeholder.py`.

### `app/ingestion` — Intelligent ingestion (Live)
`engine.py` (`DocumentIngestionEngine` — detect TEXT/SEARCHABLE_PDF/
SCANNED_PDF/IMAGE, route to text-layer or OCR), `classifier.py`
(`DocumentClassifier`).

### `app/vision` — Vision evidence (Live)
`extractor.py` (`VisionEvidenceExtractor` — OCR pages → `EvidenceReference`,
blends OCR confidence).

### `app/evidence_ai` — Claude evidence (Live)
`extractor.py` (`ClaudeEvidenceExtractor` — Claude + anti-fabrication gate,
falls back to `EvidenceExtractor`).

### `app/quality` — Quality + workbench (Live)
`engine.py` (`EvidenceQualityEngine`), `repository.py`
(`EvidenceQualityRepository`), `decision_repository.py`
(`EvidenceReviewDecisionRepository`), `workbench.py` (`ReviewerWorkbench`).

### `app/governance` — Validated mode (Live)
`engine.py` (`ValidatedEvidenceEngine`), `compliance.py`
(`GovernanceComplianceChecker`), `repository.py`
(`GovernanceSettingsRepository`).

### `app/analytics` — Quality analytics (Live)
`quality_analytics.py` (`QualityAnalyticsEngine`, `QualityAnalytics`). Uses
lazy imports to avoid a circular dependency with `app.cases`.

### `app/storage` — Persistence (Live)
`database.py` (`connect`, `initialize_schema`, `DEFAULT_DB_PATH`).

## Dependency direction (high level)

```
ui ──> cases.service ──> { agents, review, appeals, assembly, evidence,
                           evidence_ai, vision, ingestion, ocr, quality,
                           resolution, feedback, governance, analytics,
                           guidelines, audit, metrics }
                       └─> repositories ──> storage (SQLite)
all engines/agents ──> services (LLMClient)   # AI isolation seam
everything ──> models                          # shared contracts
```

No package imports `ui`. `models`, `services`, and `storage` are the shared
leaves. `analytics` imports its repositories lazily to keep the import graph
acyclic.
