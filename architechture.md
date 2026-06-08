# HealthAI Architecture

## What This Project Is

HealthAI is a local-first prior authorization review platform for healthcare documents. It ingests uploaded files, extracts structured case data, reviews the request against simplified clinical guidelines, assembles evidence across multiple documents, supports appeal generation, and persists workflow state in SQLite.

The system is designed to run in two modes:

- Deterministic offline mode.
- AI-assisted mode when an external LLM backend is configured.

The deterministic path is not just a fallback. It is a first-class execution path used by tests, local development, and safety hardening.

## Current Product Scope

The implemented product spans these major areas:

- Document upload and raw text extraction for TXT and PDF.
- Structured `PatientCase` extraction via `MedicalExtractionAgent`.
- Deterministic clinical review via `ClinicalReviewEngine`.
- Appeal generation via deterministic builder and optional LLM agent.
- Multi-document case assembly into a unified case context.
- Evidence traceability with source quotes and document/page references.
- Human conflict resolution and reviewer feedback capture.
- OCR-aware ingestion with page-level OCR result storage.
- Evidence quality scoring and reviewer workbench.
- Governance-enforced review and appeal flows.
- Payer-specific guideline packs.
- Operational health reporting and validation runner.
- Streamlit UI backed by local SQLite.

## What Is Currently Done

From the current codebase and graph index, the major implemented milestones are:

1. `M1-M4`
   Raw extraction, structured case extraction, guideline review, and appeal generation are implemented.

2. `M5-M8`
   Case management, SQLite persistence, audit logging, multi-document assembly, evidence traceability, conflict resolution, and reviewer feedback are implemented.

3. `M9-M11`
   OCR-aware ingestion, document classification, OCR result persistence, evidence quality scoring, governance filtering, and quality analytics are implemented.

4. `M12-M13 + Final`
   The original large `CaseService` was decomposed into sub-services, UI code was split into tab modules, explainability and payer-pack workflows were added, and operational/validation tooling was added.

## Current Architecture

### High-level flow

1. User uploads a document in the Streamlit UI.
2. `CaseService` routes the document through extraction or OCR-aware ingestion.
3. Structured facts and evidence are persisted in SQLite.
4. Multi-document assembly produces a `UnifiedCaseContext`.
5. Review runs against a matched guideline or payer-specific guideline pack.
6. Governance can restrict which evidence is eligible downstream.
7. Appeal generation uses the case, review, and permitted evidence.
8. Audit, analytics, explainability, and export are generated from persisted state.

### Core layers

- `app/ui`
  Streamlit dashboard and tab modules. This is the current product surface.

- `app/cases`
  Central orchestration via `CaseService` plus decomposed sub-services for ingestion, review, appeal, export, governance, analytics, payer handling, and explainability.

- `app/storage`
  SQLite connection and schema management. Default DB path is `data/healthai.db`.

- `app/agents` and `app/services`
  Structured extraction and service-layer backend abstraction. Includes deterministic local extraction (`LocalHeuristicClient`) and optional Anthropic-backed clients.

- `app/review`
  Deterministic review engine and review agent. This is where guideline criteria evaluation and many current safety fixes live.

- `app/evidence`, `app/assembly`, `app/resolution`
  Evidence extraction, evidence repositories, case assembly, conflict detection, and conflict resolution.

- `app/ocr` and `app/ingestion`
  OCR provider abstraction, Tesseract/mock providers, OCR result repository, and document routing logic for text-layer vs scanned content.

- `app/governance`, `app/quality`, `app/analytics`, `app/explainability`, `app/payers`, `app/validation`
  Governance enforcement, evidence quality, analytics, explainability, payer packs, and validation runner.

### Main architectural anchors

The graph report currently highlights these high-connectivity nodes:

- `PatientCase`
- `ReviewResult`
- `EvidenceReference`
- `CaseService`
- `CaseAssemblyEngine`
- `AuditRepository`

This matches the real design: a case-centric workflow with evidence, review, and audit built around a shared SQLite-backed orchestration layer.

## Current Persistence Model

The application is local and SQLite-backed.

- Default DB: `data/healthai.db`
- Important persisted concepts:
  - cases
  - case documents
  - audit events
  - evidence references
  - OCR results
  - conflict resolutions
  - authoritative facts
  - reviewer feedback
  - evidence quality assessments
  - evidence review decisions
  - governance settings

This makes the app operationally simple, but it also means UI behavior is tied closely to local DB state.

## Current Runtime Backends

### Extraction and review backends

- Deterministic local extraction: `LocalHeuristicClient`
- Deterministic review: `ClinicalReviewEngine`
- Optional AI backends through `LLMClient` abstraction

### OCR backends

- `LocalTesseractOCRProvider` for real OCR when dependencies are installed
- `MockOCRProvider` as deterministic fallback when Tesseract is unavailable

Important current behavior:

- TXT files do not produce OCR rows.
- Searchable PDFs do not produce OCR rows.
- OCR Explorer only shows persisted OCR page results for scanned PDFs/images.
- If Tesseract is not installed, real OCR is not available even though ingestion still works.

## Current UI State

The current UI is functional but operationally dense.

- It is Streamlit-based.
- It is split into many tabs and sub-tabs.
- Some workflows are accessible but not obvious.
- Some empty states are technically correct but misleading to users.

Known example:

- OCR Explorer is a viewer for existing OCR results, not an action tab.
- Users can upload TXT or searchable PDF documents and still see "No OCR results yet," which is correct from the code’s perspective but confusing from a product perspective.

## What Remains

The system is feature-rich, but not yet clean enough to be considered finished. The remaining work is mostly in these areas:

- UI simplification and workflow redesign.
- OCR workflow clarity and real OCR setup experience.
- Deterministic extraction/review hardening for messy real-world denial text.
- Better separation between educational/policy text and patient evidence.
- Reduction of architectural complexity around `CaseService` and old parallel modules.
- Desktop packaging strategy if the app is to be shipped outside a dev environment.
- Production-readiness tasks such as app-data DB location, process lifecycle, packaging, and signing.

## Current Architecture Strengths

- Strong offline-first story.
- Good test coverage for many pipeline behaviors.
- Clear typed models around cases, reviews, evidence, and governance.
- SQLite persistence is simple and practical.
- Deterministic engines make debugging and regression testing feasible.
- Evidence traceability and governance layers are stronger than typical prototype systems.

## Current Architecture Weaknesses

- UI and workflow complexity are high for end users.
- `CaseService` is still a major coordination hub.
- Some workflow state is correct technically but confusing operationally.
- OCR behavior is easy to misunderstand because ingestion and OCR are not the same thing.
- Real OCR depends on local environment setup that is not enforced by the app.
- There is still technical debt from parallel or partially superseded modules noted in the existing architecture docs.

## Current Validation State

At the latest verified run in this workspace:

- `515` tests passed.
- `1` test failed.

Known remaining failing test:

- `app/tests/test_patient_case_model.py::TestDerived::test_summary_for_pending_is_neutral`

That failure is unrelated to the recent deterministic extraction/review fixes, but it means the suite is not yet fully green.

## Summary

HealthAI is currently a substantial local case-review platform, not a prototype toy. The core pipeline exists end to end: ingest -> extract -> assemble -> review -> govern -> appeal -> audit/export.

What remains is not "build the system from scratch." What remains is to reduce ambiguity, harden edge cases, simplify the UI, resolve the last known defects, and make the product easier to operate reliably.
