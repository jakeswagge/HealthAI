# HealthAI — System Overview

> Documentation only. This describes the architecture as it currently exists.
> No business logic was changed to produce these docs.

## What HealthAI is

HealthAI is a local, Python-based **healthcare prior-authorization assistant**.
It ingests insurance documents (TXT, searchable/scanned PDF, images), extracts
structured data, reviews requests against clinical guidelines, drafts appeal
letters, and runs a full reviewer-governed case-management workflow — with
end-to-end traceability and an append-only audit trail.

It runs entirely on a developer machine: a Streamlit UI, a local SQLite store,
and an LLM service layer that uses a hosted backend when configured and
deterministic offline backends otherwise.

## Top-level shape

```
streamlit_app.py            # entry point -> app.ui.dashboard.render_dashboard()
app/
├── ui/                     # Streamlit dashboard (19 tabs) + session caching
├── models/                 # pydantic data models (the contracts)
├── services/               # LLM service layer (Claude / local / mock)
├── extraction/             # raw text + size validation (M1)
├── agents/                 # MedicalExtractionAgent (M2)
├── guidelines/             # clinical guideline library (M3)
├── review/                 # ClinicalReviewEngine + GuidelineReviewAgent (M3)
├── appeals/                # AppealGenerationAgent + builder (M4)
├── cases/                  # CaseService + repositories + transitions + export (M5..)
├── audit/                  # append-only audit log (M5)
├── metrics/                # operational metrics (M5)
├── evidence/               # deterministic evidence extraction + linker (M6/7)
├── assembly/               # CaseAssemblyEngine -> UnifiedCaseContext (M6/7)
├── resolution/             # human conflict resolution + authoritative facts (M8)
├── feedback/               # reviewer feedback + learning dataset (M8)
├── ocr/                    # OCR abstraction + Tesseract/mock providers (M9)
├── ingestion/              # DocumentIngestionEngine + classifier (M9)
├── vision/                 # VisionEvidenceExtractor (OCR -> evidence) (M9)
├── evidence_ai/            # ClaudeEvidenceExtractor (anti-fabrication gate) (M10)
├── quality/                # quality scoring + ReviewerWorkbench (M10)
├── governance/             # validated-evidence mode + compliance (M11)
├── analytics/              # quality + workflow analytics (M11)
└── storage/                # SQLite connection + schema
```

## Core design principles (observed in code)

1. **AI is isolated behind a service layer.** Everything that calls a model
   goes through `app/services/llm_client.LLMClient`. Concrete backends:
   `AnthropicClient` (real Claude), `GeminiClient` (real Gemini),
   `LocalHeuristicClient` (offline regex), `MockClaudeClient` (tests).
   `get_llm_client()` auto-selects.
2. **Offline-first, deterministic fallback.** Every AI-backed agent
   (extraction, review, appeal, evidence) falls back to a deterministic engine,
   so the app and the full test suite run with no API key.
3. **Traceability is the backbone.** Facts are carried as `EvidenceReference`
   objects (source document + page + verbatim quote + confidence). Review,
   appeal, and exports cite evidence ids.
4. **Reviewer authority + auditability.** Humans resolve conflicts, validate
   evidence, and approve cases. Every significant action writes an
   `AuditEvent`. Rejected evidence is never used in validated mode.
5. **Additive, idempotent persistence.** The SQLite schema is created with
   `CREATE TABLE IF NOT EXISTS`; each milestone added tables without breaking
   older ones.
6. **`CaseService` is the orchestrator.** The UI talks to one façade
   (`app.cases.service.CaseService`) that wires the repositories and engines
   together and records audit events.

## Runtime backends

| Concern | Real backend | Offline / test backend |
|---------|--------------|------------------------|
| LLM | `AnthropicClient` (Claude), `GeminiClient` (Gemini) | `LocalHeuristicClient`, `MockClaudeClient` |
| OCR | `LocalTesseractOCRProvider` | `MockOCRProvider` |
| Storage | SQLite file `data/healthai.db` | `":memory:"` in tests |

Selection: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, and
`HEALTHAI_LLM_BACKEND` for the LLM;
`get_ocr_provider()` probes for Tesseract and falls back to the mock.

## High-level pipeline

```
upload ──> ingestion (detect text-layer vs OCR) ──> CaseDocument(s)
      ──> evidence extraction (regex or Claude) ──> EvidenceReference(s)
      ──> assembly (merge, conflicts, missing) ──> UnifiedCaseContext + PatientCase
      ──> quality scoring + reviewer workbench (approve/reject/flag)
      ──> governance filter (draft vs validated) ──> evidence for consumption
      ──> clinical review ──> ReviewResult
      ──> appeal generation ──> AppealLetter
      ──> human review ──> APPROVED_FOR_EXPORT / REJECTED
      ──> export bundle (ZIP)
      └─> every step: AuditEvent + traceable evidence references
```

See `workflow_map.md`, `evidence_flow.md`, and `governance_flow.md` for detail,
and `diagrams/` for PlantUML renderings.

## A note on parallel modules

Two lineages of evidence/assembly code exist in the tree. The **live** path
(wired into `CaseService` and the UI) is:
`app/evidence/*`, `app/assembly/engine.py`, `app/models/evidence_reference.py`,
and `CaseService.assemble_case`.

An **alternate, self-contained** lineage also exists but is not imported by the
live service/UI: `app/cases/assembly_service.py`,
`app/cases/evidence_repository.py`, `app/models/evidence.py`, and
`app/assembly/traceability.py`. It is documented here for completeness and
flagged in `package_map.md` so future work can converge or remove it.
