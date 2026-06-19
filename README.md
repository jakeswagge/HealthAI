# HealthAI

A Python-based Healthcare **Prior Authorization AI Agent**.

This repository implements three milestones:

- **Milestone 1** — upload healthcare documents (PDF / TXT) and view extracted
  raw text.
- **Milestone 2** — AI medical extraction: convert documents into a structured,
  validated `PatientCase` (hosted LLM backends behind a service layer, with an
  offline deterministic fallback).
- **Milestone 3** — clinical guideline decision engine: review a requested
  service against medical-necessity guidelines and return a structured
  `ReviewResult` (APPROVE / DENY / INSUFFICIENT_INFORMATION) with matched and
  missing criteria, missing evidence, recommended actions, and a rationale.

---

## Features

**Milestone 1 — document extraction**
- 🖥️ Streamlit dashboard
- 📄 PDF + 📝 TXT upload and text extraction
- 🔎 File validation, 📊 document metadata

**Milestone 2 — structured AI extraction**
- 🤖 `MedicalExtractionAgent` → validated `PatientCase` (pydantic)
- 🔌 AI isolated behind a service layer (`app/services`); Claude or Gemini when
  configured, deterministic offline backend otherwise
- 🔁 JSON validation with retry, confidence scoring, evaluation framework

**Milestone 3 — clinical review**
- 📚 Clinical guideline library (local JSON): Humira, Enbrel, MRI Lumbar Spine,
  CT Chest, Physical Therapy
- ⚖️ `ClinicalReviewEngine` (deterministic) + `GuidelineReviewAgent` (Claude)
- 🧭 Recommendation, matched/missing criteria, missing evidence, next actions
- 🧪 Review evaluation framework (recommendation accuracy, schema compliance,
  JSON validity, guideline-matching accuracy)

**Milestone 4 — appeal generation**
- ✍️ `AppealGenerationAgent` (Claude) + `AppealLetterBuilder` (deterministic
  offline fallback) → validated `AppealLetter`
- 📄 Full appeal letter (8 required sections) challenging the denial, citing
  guideline support, and flagging missing evidence
- 🛡️ Safety: never fabricates clinical facts; uses "Documentation was not
  available" / "Additional clinical evidence may be required"
- ⬇️ Download as TXT or Markdown

**Milestone 5 — case management, human review, audit & metrics**
- 🗂️ `CaseRecord` lifecycle tracking persisted in local **SQLite**
- 👤 Human review (approve / reject / request changes) with status transitions
- 📜 Append-only audit log of every significant action
- 📊 Operational metrics dashboard (approval/rejection/fallback rates, etc.)
- 📦 Export package (ZIP: summary + JSON artifacts + audit log)

**Milestone 6 & 7 — multi-document assembly + evidence traceability**
- 📚 A case holds many documents (denial, clinical note, lab, imaging, referral,
  prior-auth form), auto-classified and stored in SQLite
- 🧩 `CaseAssemblyEngine` → `UnifiedCaseContext`: merged evidence, resolved
  facts, conflict report, missing-info detection
- 🔎 `EvidenceReference` traces every fact to a source document + page + quote
- 🪪 Traceable review + appeal: evidence ids on recommendations and each appeal
  section; unsupported statements are flagged, never fabricated
- ⚠️ Conflict detection with severity (HIGH/MEDIUM/LOW)
- 📦 Export adds `evidence_inventory.json`, `conflict_report.json`,
  `traceability_report.md`

**Milestone 8 — human conflict resolution + reviewer feedback**
- 🧑‍⚖️ Reviewers resolve conflicts and set the authoritative case record;
  rejected values are preserved and every decision is audited
- 🏛️ `AuthoritativeFact` (SYSTEM auto-resolved or HUMAN-decided); review +
  appeal use human-chosen values
- 📝 Structured `ReviewerFeedback` (CORRECT/INCORRECT/PARTIAL) per stage,
  collected into an exportable learning dataset (no ML, no retraining)
- 📦 Export adds `authoritative_facts.json`, `conflict_resolutions.json`,
  `reviewer_feedback.json`

**Milestone 9 — OCR + intelligent ingestion + vision evidence**
- 📷 Ingest scanned PDFs, faxes, and images (PNG/JPG/JPEG); auto-detects
  text-layer vs scanned and OCRs only when needed
- 🔡 `LocalTesseractOCRProvider` (offline) with graceful degradation to a
  deterministic mock provider when Tesseract is absent — workflow never crashes
- 🧾 `OCRPageResult` per page (text, confidence, processing method); evidence
  blends OCR confidence so uncertainty is never hidden
- 🗂️ `DocumentClassifier` (auto + manual override); OCR evidence flows into
  assembly, conflicts, authoritative facts, review, and appeal unchanged
- 🚦 Configurable OCR confidence gate; low-confidence pages flagged, never
  silently accepted; reviewers inspect source text in the OCR Explorer
- 📦 Export adds `ocr_results.json`, `document_classification.json`,
  `ocr_traceability_report.md`

**Milestone 10 — Claude evidence extraction + quality scoring + workbench**
- 🤖 `ClaudeEvidenceExtractor` with an anti-fabrication gate (every quote must
  appear verbatim in the source), falling back to the deterministic extractor
- 📐 `EvidenceQualityEngine` scores completeness/relevance/consistency/
  traceability and flags weak, duplicate, conflicting, and missing-support
  evidence (plus unsupported appeal statements)
- 🧑‍⚕️ `ReviewerWorkbench`: per-item source quote + quality + relations, with
  Approve / Reject / Flag; rejected evidence is excluded downstream
- 📦 Export adds `evidence_quality.json`, `evidence_review_decisions.json`

**Milestone 11 — validated evidence mode + governance + quality analytics**
- 🛡️ `GovernanceSettings`: validated-evidence mode, min quality score,
  unreviewed-evidence gate, conflict-resolution + human-review export gates
- ✅ `ValidatedEvidenceEngine` produces an `ApprovedEvidenceSet` (included vs.
  excluded + reasons); rejected evidence is never used in validated mode
- 🧯 `GovernanceComplianceChecker` flags weak-evidence appeals, unresolved
  conflicts, export-without-review, and low-quality-evidence usage
- 📈 `QualityAnalyticsEngine`: approval/rejection/flag rates, average quality,
  weak/conflict rates, review turnaround, appeal success rate
- 📦 Export adds `governance_report.json`, `quality_analytics.json`,
  `approved_evidence.json`, `excluded_evidence.json`

**Architecture hardening**
- 🧪 `MockClaudeClient` — realistic Claude stand-in for tests (valid/missing/
  invalid/markdown/hallucinated/truncated responses) exercising the full
  validation + retry path
- 🗃️ Session-state caching: LLM calls run only on new uploads or explicit
  reprocess; tab switches never trigger Claude (see
  [`docs/caching.md`](docs/caching.md))
- 📏 `DocumentSizeValidator` — detect-and-warn on page/character/token size (no
  chunking, no RAG)
- 🔌 OCR abstraction (`app/ocr/`) — interfaces + placeholder only (no engine)

---

## Project structure

```
HealthAI/
├── app/
│   ├── ui/              # Streamlit dashboard (19 tabs) + session caching
│   ├── extraction/      # validation + raw text extraction + size validator
│   ├── agents/          # MedicalExtractionAgent + prompts + eval (M2)
│   ├── services/        # LLM service layer: Claude + Gemini + local + mock
│   ├── guidelines/      # clinical guideline library: load + match (M3)
│   ├── review/          # ClinicalReviewEngine + GuidelineReviewAgent (M3)
│   ├── appeals/         # AppealGenerationAgent + letter builder (M4)
│   ├── cases/           # case mgmt: repos + service + transitions + export (M5/6/7)
│   ├── audit/           # audit logging repository (M5)
│   ├── metrics/         # operational metrics collector (M5)
│   ├── evidence/        # evidence extractor + linker + repository (M6/7)
│   ├── assembly/        # CaseAssemblyEngine -> UnifiedCaseContext (M6/7)
│   ├── resolution/      # conflict resolution engine + repositories (M8)
│   ├── feedback/        # reviewer feedback repo + learning dataset (M8)
│   ├── ocr/             # OCR abstraction + Tesseract/mock providers + repo (M9)
│   ├── ingestion/       # DocumentIngestionEngine + classifier (M9)
│   ├── vision/          # VisionEvidenceExtractor (OCR -> evidence) (M9)
│   ├── evidence_ai/     # ClaudeEvidenceExtractor (anti-fabrication gate) (M10)
│   ├── quality/         # EvidenceQualityEngine + ReviewerWorkbench + repos (M10)
│   ├── governance/      # ValidatedEvidenceEngine + compliance + settings (M11)
│   ├── analytics/       # QualityAnalyticsEngine (M11)
│   ├── storage/         # SQLite connection + schema (M5..M11)
│   ├── models/          # pydantic data models
│   └── tests/           # pytest suite
├── data/
│   ├── sample_docs/     # mock approval/denial + multi-document samples
│   ├── guidelines/      # clinical guideline JSON library (M3)
│   └── healthai.db      # local SQLite case store (gitignored, auto-created)
├── docs/                # milestone docs + caching.md + environment.md
├── requirements.txt
├── pytest.ini
├── README.md
└── streamlit_app.py     # Streamlit entry point
```

---

## Requirements

- **Python 3.12 — supported (recommended).** **Python 3.13 — experimental**
  (validated locally; see [`docs/environment.md`](docs/environment.md)).
- Libraries: `streamlit`, `pymupdf`, `pydantic`, `pytest`, and optional hosted
  LLM SDKs (`anthropic`, `google-genai`; the app runs offline without keys).

See [`docs/environment.md`](docs/environment.md) for dependency compatibility
considerations (compiled wheels, optional SDK, offline-first testing).

### Using a hosted LLM backend (optional)

```powershell
pip install anthropic google-genai

# Claude
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# optional model override (defaults to claude-opus-4-8):
$env:HEALTHAI_CLAUDE_MODEL = "claude-opus-4-8"

# Gemini on Vertex AI (recommended; uses ADC, no AI Studio API key)
gcloud auth application-default login
gcloud config set project skilled-loader-468413-j6
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\Users\jakes\Downloads\skilled-loader-468413-j6-50ec35997585.json"
$env:HEALTHAI_LLM_BACKEND = "gemini"
$env:GOOGLE_CLOUD_PROJECT = "skilled-loader-468413-j6"
$env:GOOGLE_CLOUD_LOCATION = "global"
$env:HEALTHAI_STRUCTURED_EXTRACTION_BACKEND = "gemini"
$env:HEALTHAI_CLINICAL_REASONING_BACKEND = "gemini"
$env:HEALTHAI_APPEAL_DRAFTING_BACKEND = "gemini"
# optional model override (defaults to gemini-3.5-flash):
$env:HEALTHAI_GEMINI_MODEL = "gemini-3.5-flash"
# optional thinking override (defaults to 0 for structured JSON):
$env:HEALTHAI_GEMINI_THINKING_BUDGET = "0"

# Optional Vertex smoke-test chat app:
streamlit run vertex_gemini_chat_app.py
```

Without ADC, an API key, or another configured hosted provider, extraction and
review use the deterministic offline backends.

If you prefer a service account instead of interactive ADC, set
`GOOGLE_APPLICATION_CREDENTIALS` to the local JSON key path for that account.
Do not commit the key file into the repository.

---

## Installation

### 1. Clone / open the project

```bash
cd HealthAI
```

### 2. Create and activate a virtual environment

**Windows (PowerShell):**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks script activation, run once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**Windows (cmd):**

```cmd
py -3.12 -m venv .venv
.venv\Scripts\activate.bat
```

**macOS / Linux:**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

> No Python 3.12 installed? Use your default `python` / `py` in the commands
> above. The app is verified to run on Python 3.13 as well.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## Running the tests

From the project root, with the virtual environment active:

```bash
pytest
```

You should see all tests pass. Tests cover:

- TXT extraction
- PDF extraction (a PDF is generated in-memory during the test)
- File validation

---

## Starting the Streamlit app

From the project root:

```bash
streamlit run streamlit_app.py
```

Streamlit prints a local URL (typically <http://localhost:8501>). Open it in
your browser.

### Try it with the sample data

1. In the sidebar, download one of the bundled samples
   (`approval_case_01.txt` or `denial_case_01.txt`), **or** use any PDF/TXT.
2. Upload it with the file uploader.
3. View the extracted text and document metadata.

Sample documents live in [`data/sample_docs/`](data/sample_docs).

---

## Roadmap (later milestones)

- AI-based field extraction from documents
- Clinical guideline matching
- Automated appeal letter generation
