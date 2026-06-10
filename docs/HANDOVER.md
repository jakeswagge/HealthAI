# HealthAI - Engineer / AI Agent Handover

> Goal: make someone unfamiliar with HealthAI productive within 30 minutes.
> Read this top to bottom, then open the files in the "Where To Start Reading"
> order. Pair this with `docs/ARCHITECTURE_COMPLETE.md` for depth.

---

## 1. Project Summary

HealthAI is a **local, Python prior-authorization document intelligence**
platform. It ingests healthcare documents, extracts structured facts with full
source traceability, reviews requests against clinical guidelines, and drafts
appeal letters - always keeping a human reviewer in authority and recording an
audit trail. It runs fully offline by default and upgrades to Claude or Gemini
when a hosted LLM backend is configured.

---

## 2. Current Status

- **Milestones completed:** M1-M13 plus the Final Milestone (payer packs,
  operational hardening, production-readiness docs). See the table in
  `docs/ARCHITECTURE_COMPLETE.md` section 3.
- **Tests:** **402 passing** (`python -m pytest -q`).
- **UI:** Streamlit, 23 tabs.
- **Maturity:** validation-ready, not unattended-production. Decision-support
  only; a human signs off on every output. See `docs/production_readiness.md`.

---

## 3. Key Architectural Decisions

- **Why governance exists** - organizations need to operate on
  *reviewer-approved* evidence only. Governance (`app/governance`) lets them
  switch from "draft" (all evidence) to "validated" (approved-only,
  quality-gated) and proves which evidence was permitted.
- **Why evidence traceability exists** - clinical decisions must be defensible.
  Every fact is an `EvidenceReference` tied to a source document, page, and
  verbatim quote, so any output can be traced to its origin.
- **Why reviewer authority overrides AI** - safety. A human REJECT on a piece of
  evidence is absolute: in validated mode the agents never even see rejected
  evidence (the `PatientCase` is synthesized from only the permitted subset).
- **Why payer packs were implemented** - different payers apply different
  medical-necessity criteria. Guideline packs (`app/payers`) let the same case
  be reviewed under different (mock) payer policies with provenance recorded.

---

## 4. Where To Start Reading (exact order)

1. `docs/ARCHITECTURE_COMPLETE.md` - the big picture (this is the map).
2. `app/cases/service.py` - the `CaseService` facade; the entry point for every
   workflow operation. Skim the `__init__` to see how everything is wired.
3. `app/models/patient_case.py` - the central structured case model.
4. `app/models/evidence_reference.py` - the canonical evidence model.
5. `app/assembly/engine.py` - how documents become a `UnifiedCaseContext` + a
   `PatientCase`.
6. `app/governance/engine.py` + `app/models/governance.py` - how evidence is
   filtered (`ApprovedEvidenceSet`).
7. `app/review/engine.py` + `app/review/review_agent.py` - how a decision is
   made.
8. `app/appeals/builder.py` + `app/appeals/appeal_agent.py` - how an appeal is
   drafted.
9. `app/explainability/engine.py` + `app/cases/explainability_service.py` - how
   governance-enforced, explainable reviews/appeals are produced.
10. `app/payers/packs.py` + `app/cases/payer_service.py` - payer pack resolution
    and integration.
11. `app/storage/database.py` - the schema and persistence strategy.
12. `app/ui/dashboard.py` + `app/ui/tabs/` - how the UI calls the facade.

---

## 5. Most Important Classes

| Class | Location | Role |
| --- | --- | --- |
| `CaseService` | `app/cases/service.py` | **The facade.** Single entry point; owns the connection + repositories; delegates to sub-services. |
| `CaseAssemblyEngine` | `app/assembly/engine.py` | Merges evidence into a `UnifiedCaseContext`; synthesizes the `PatientCase`; detects conflicts. |
| `EvidenceExtractor` / `ClaudeEvidenceExtractor` | `app/evidence` / `app/evidence_ai` | Deterministic vs Claude evidence extraction (both anti-fabrication safe). |
| `ValidatedEvidenceEngine` | `app/governance/engine.py` | Builds the `ApprovedEvidenceSet` (enforces reviewer authority). |
| `GuidelineReviewAgent` / `ClinicalReviewEngine` | `app/review` | Produce a `ReviewResult` (agent falls back to the deterministic engine). |
| `AppealGenerationAgent` / `AppealLetterBuilder` | `app/appeals` | Produce an `AppealLetter` (agent falls back to the builder). |
| `ExplainabilityEngine` | `app/explainability/engine.py` | Builds `ReviewExplanation` / `AppealExplanation` / `TraceabilityChain`. |
| `ReviewerWorkbench` / `EvidenceQualityEngine` | `app/quality` | Reviewer decisions + evidence quality scoring. |
| `GuidelinePackResolver` / `PayerRepository` | `app/payers` | Pack resolution + payer profiles. |
| `OperationalHealthMonitor` | `app/operations/health.py` | Local operational diagnostics. |
| `ValidationRunner` | `app/validation/runner.py` | Runs mock scenarios through the full pipeline. |
| `AuditRepository` | `app/audit/repository.py` | Append-only audit trail. |
| `GuidelineReviewAgent` repository | `app/guidelines/repository.py` | Loads + matches clinical guidelines. |

---

## 6. Most Important Data Models (`app/models`)

| Model | Role |
| --- | --- |
| `PatientCase` | Structured case (patient, diagnosis, codes, decision) with per-field source attribution. |
| `EvidenceReference` | **Canonical** source-backed fact (doc, page, quote, fact_type, confidence). |
| `UnifiedCaseContext` | Assembly output: evidence + resolved facts + conflict report + `PatientCase`. |
| `ConflictReport` / `FactConflict` | Cross-document disagreements with severity. |
| `ConflictResolution` / `AuthoritativeFact` | Human resolution + the resulting authoritative value. |
| `EvidenceQualityAssessment` | Four sub-scores + `overall_score` + `is_weak`. |
| `EvidenceReviewDecision` | Reviewer APPROVE/REJECT/FLAG on one evidence reference. |
| `GovernanceSettings` / `ApprovedEvidenceSet` | Policy knobs + the filtered evidence selection. |
| `ReviewResult` | Recommendation, matched/missing criteria, rationale, confidence, payer provenance. |
| `AppealLetter` | Structured appeal + rendered `letter_text` + payer provenance. |
| `ReviewExplanation` / `AppealExplanation` / `TraceabilityChain` | Explainability artifacts. |
| `PayerProfile` | Payer + which guideline pack/version it uses. |
| `OperationalHealthReport` | Operational diagnostics snapshot. |
| `AuditEvent` | One immutable audit-trail entry. |
| `CaseRecord` | Workflow envelope persisted in the `cases` table. |

---

## 7. Common Development Tasks

> All of these are additive and require **no schema change** (artifacts persist
> as JSON; packs/profiles/guidelines are files).

### Add a new payer
1. Create `data/payers/<name>.json` with `payer_id`, `payer_name`,
   `guideline_pack`, `version`, `status` (see existing files).
2. If it needs custom criteria, create `data/guideline_packs/<PACK>/*.json`
   overrides (see "Add a new guideline").
3. `get_payer_repository(force_reload=True)` picks it up; it appears in the
   Payer Management tab automatically.

### Add a new guideline
1. Add a JSON file to `data/guidelines/` (base) matching the
   `ClinicalGuideline` schema (`guideline_id`, `service_name`, `diagnosis`,
   `required_criteria`, `contraindications`, `version`, `source`, matching aids).
2. For a payer-specific variant, put a file with the **same `guideline_id`** in
   `data/guideline_packs/<PACK>/`; it overrides the base for that pack.

### Add a new evidence extractor
1. Produce `EvidenceReference` objects using the **canonical** model
   (`app/models/evidence_reference.py`) - never fabricate values; always include
   `quoted_text`.
2. Follow the pattern in `app/evidence/extractor.py` (deterministic) or
   `app/evidence_ai/extractor.py` (Claude + verbatim-quote gate).
3. Wire it where evidence is gathered (assembly/ingestion) so it flows through
   quality scoring, governance, and traceability unchanged.

### Add a new export file
1. Add an optional parameter to `build_export_files` in `app/cases/export.py`
   and write the new filename -> content into the `files` dict (guarded by
   `if <param> is not None`).
2. Forward the parameter through `build_export_zip`.
3. Pass the data from the export call site in `app/ui/tabs/case_tabs.py`.
   Keep it backward-compatible (defaults `None`).

### Add a new review criterion
1. Add a `GuidelineCriterion` (id, description, `keywords`, `required`) to the
   relevant guideline JSON.
2. The deterministic engine (`ClinicalReviewEngine`) evaluates it via keyword
   presence in supporting vs. denial text; no code change needed for keyword
   criteria. Re-run the review to see the effect.

---

## 8. How To Run

### Environment setup
- Python 3.13 in a virtualenv at `.venv` (already present).
- Windows PowerShell: use `;` to chain commands (not `&&`).
- Interpreter: `.\.venv\Scripts\python.exe`.

### Tests
```
.\.venv\Scripts\python.exe -m pytest -q
```
Expect **402 passed**.

### Streamlit (run manually in your own terminal)
```
.\.venv\Scripts\python.exe -m streamlit run app/ui/dashboard.py
```
(Do not launch long-running servers from automation; run it yourself.)

### Validation runner
```
.\.venv\Scripts\python.exe -m validation.run          # summary, exit 0/1
.\.venv\Scripts\python.exe -m validation.run --json    # full JSON report
```

### LLM backend
- Default: offline `LocalHeuristicClient` (no key needed).
- Claude: set `ANTHROPIC_API_KEY` (and `pip install anthropic`); force with
  `HEALTHAI_LLM_BACKEND=anthropic`.
- Gemini: set `GEMINI_API_KEY` or `GOOGLE_API_KEY` (and `pip install
  google-genai`); force with `HEALTHAI_LLM_BACKEND=gemini`.
- Local deterministic: use no hosted key, or force with
  `HEALTHAI_LLM_BACKEND=local`.

---

## 9. How To Debug

- **Where logs are** - there is no log file. Diagnostics come from (1) the audit
  trail in SQLite, (2) the Operational Health tab / `service.operational_health()`,
  and (3) stdout warnings from loaders. Inspect the DB at `data/healthai.db`.
- **How audit records work** - every significant action calls
  `AuditRepository.log(case_id, event_type, details, actor)`. Audit is
  append-only; query with `service.history(case_id)` or `audit.by_type(...)`.
  Failures/degradations surface as `details` text that `OperationalHealthMonitor`
  scans.
- **How traceability works** - call `service.traceability_chain(case_id)` to get
  a `TraceabilityChain` linking each evidence id to source doc/page, reviewer
  decision, quality score, and included/excluded status. Exports include
  `traceability_chain.json` and `traceability_report.md`.
- **How governance filtering works** - `service.evidence_for_consumption(case_id,
  settings)` returns the permitted evidence + the `ApprovedEvidenceSet`. In
  validated mode, REJECTED is always excluded; sub-threshold quality is excluded;
  unreviewed may be excluded depending on `allow_unreviewed_evidence`. Use
  `service.generate_governed_review/appeal` to see the constrained outcome and
  its explanation.

---

## 10. Known Risks

### Current limitations
- No authentication / authorization / transport security (local single-tenant).
- SQLite: single-writer, low-concurrency.
- Offline by default: real Claude and real OCR behavior are not yet validated.

### Technical debt
- Dead parallel evidence lineage (`app/cases/assembly_service.py`,
  `app/cases/evidence_repository.py`, `app/models/evidence.py`,
  `app/assembly/traceability.py`) - zero live importers; guarded by an
  architecture test; removal deferred (needs file-deletion approval).
- Mock OCR (`MockOCRProvider`) and mock/local LLM stand-ins.
- See `docs/ARCHITECTURE_COMPLETE.md` section 12 for the full list.

### Future priorities
Real Claude validation -> real OCR validation -> payer policy ingestion ->
authentication -> multi-user -> cloud deployment -> HIPAA hardening
(see Architecture doc section 13).

---

## 11. Rules Future Engineers / Agents MUST Follow

These are the platform's trust guarantees. Do not weaken them.

1. **Do not bypass governance.** Downstream review/appeal must consume evidence
   via the governance path (`evidence_for_consumption` /
   `generate_governed_review` / `generate_governed_appeal`). Do not feed raw,
   unfiltered evidence to agents in validated workflows.
2. **Do not bypass evidence traceability.** Every fact must remain an
   `EvidenceReference` with a source document, page, and verbatim quote. Do not
   introduce facts that lack provenance.
3. **Do not bypass reviewer authority.** REJECTED evidence must never influence a
   governed review/appeal. Do not "re-include" rejected evidence under any
   setting.
4. **Do not generate unsupported evidence.** No fabrication: the Claude extractor
   gates on verbatim quotes; the deterministic extractor only restates found
   values. Keep this gate intact.
5. **Do not remove auditability.** The audit trail is append-only. Do not delete
   or mutate audit events, and keep recording an `AuditEvent` for every
   significant action.

Additional guardrails for this phase: no schema changes, no destructive
operations, no file deletions without approval, and keep all 402 tests green.

---

_New here? Read `docs/ARCHITECTURE_COMPLETE.md`, skim `app/cases/service.py`,
run the tests, then open the Streamlit app and click through the tabs in order._
