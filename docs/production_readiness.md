# HealthAI Production Readiness Checklist

This document captures the operational assumptions, requirements, and known
limitations for running HealthAI beyond local development. HealthAI is a
decision-support tool for prior-authorization workflows; it is not an
autonomous decision-maker. A qualified human must review every output before
any external use.

> Status: prepared for real-world *validation*, not unattended production use.
> All payer guideline packs bundled here are simplified mock policies and
> contain no proprietary payer content.

## 1. Configuration

| Concern | Setting | Notes |
| --- | --- | --- |
| LLM backend | `HEALTHAI_LLM_BACKEND` = `anthropic` \| `gemini` \| `local` | Auto-detects: Anthropic when `ANTHROPIC_API_KEY` is set, then Gemini when `GEMINI_API_KEY` / `GOOGLE_API_KEY` is set, otherwise the offline deterministic backend. |
| Claude credentials | `ANTHROPIC_API_KEY` | Never commit. Inject via environment / secrets manager. |
| Gemini credentials | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Never commit. Inject via environment / secrets manager. |
| Database path | `app.storage.database.DEFAULT_DB_PATH` (default `data/healthai.db`) | Pass an explicit `db_path`/`conn` to `CaseService` to relocate. |
| Guideline packs | `data/guideline_packs/<PACK>/*.json` | Overlay files override base `data/guidelines/` by `guideline_id`. |
| Payer profiles | `data/payers/*.json` | A built-in `DEFAULT` profile always exists. |
| Governance policy | `GovernanceSettings` (persisted) | Draft vs validated mode, quality threshold, review/conflict gates. |

Configuration is file/environment based. There is no network service to
configure; the app runs as a local Streamlit process.

## 2. Backups

- The entire application state is a single SQLite file (`data/healthai.db`)
  plus the JSON content under `data/` (guidelines, packs, payers).
- Back up by copying the SQLite file while the app is stopped, or use
  `sqlite3 data/healthai.db ".backup backup.db"` for a consistent online copy.
- Guideline packs, payer profiles, and validation datasets are plain files and
  should be version-controlled.
- Exports (per-case ZIP bundles) are reproducible from the database and are not
  themselves a backup mechanism.

## 3. Database management

- Schema is created idempotently on startup via `initialize_schema` (safe to
  call repeatedly; uses `CREATE TABLE IF NOT EXISTS`).
- No destructive migrations are performed automatically. Schema changes are an
  explicit, reviewed operation.
- SQLite is appropriate for single-node, low-concurrency use. For multi-user or
  high-concurrency deployments, plan a migration to a server-backed database
  (out of scope for this milestone).
- Audit events are append-only; do not edit or delete them - they are the
  traceability backbone.

## 4. Security assumptions

- HealthAI assumes a trusted, single-tenant local environment. There is no
  built-in authentication, authorization, or network transport security.
- If exposed beyond localhost, it MUST be placed behind an authenticating
  reverse proxy / VPN; the app does not authenticate users itself.
- Secrets (e.g. `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) are read from the
  environment and must be managed by the host's secrets mechanism. Secrets are
  never written to the database or exports.
- Uploaded documents and extracted text are stored locally in SQLite without
  encryption at rest; rely on host disk encryption for confidentiality.
- External content (LLM outputs, fetched/parsed documents) is treated as
  untrusted data and is never executed.

## 5. PHI handling assumptions

- Documents processed are expected to contain PHI. All processing is local; no
  PHI leaves the host EXCEPT the text sent to the explicitly enabled hosted LLM
  backend. With the local backend, no data leaves the machine.
- Operators enabling Claude or Gemini are responsible for a BAA / applicable
  data-processing agreement and for confirming their provider configuration
  meets their compliance obligations.
- Sample/validation data is synthetic; no real PHI is bundled.
- Exports may contain PHI; treat export ZIPs as sensitive and store/transmit
  them accordingly.
- PII in code examples uses placeholders; real PII appears only in operator
  data.

## 6. Operational limitations

- OCR uses a deterministic offline stand-in unless Tesseract + `pytesseract` are
  installed; low-confidence pages are flagged, never silently trusted.
- The deterministic review/appeal engines match against structured evidence and
  keyword criteria; they are intentionally conservative and may return
  `INSUFFICIENT_INFORMATION` when documentation is incomplete.
- Guideline packs are simplified mock policies for demonstration/validation; they
  are not a substitute for a payer's actual medical policy.
- Single-node SQLite: not designed for concurrent multi-writer workloads.
- Operational health and analytics are computed on demand from local data; there
  is no background collection or external observability integration.

## 7. Human-review requirements

- Every generated review and appeal is decision-support only and MUST be
  reviewed and signed off by a qualified human before external submission.
- Reviewer authority always wins: in validated governance mode, evidence a
  reviewer rejected can never influence a review/appeal (enforced and tested).
- Governance can require human review before export
  (`require_human_review_before_export`) and conflict resolution
  (`require_conflict_resolution`); enable these for higher-assurance workflows.
- The audit trail records every significant action; reviewers should rely on the
  traceability chain and explainability reports to verify evidence lineage.

## 8. Pre-launch validation checklist

- [ ] `python -m pytest -q` is green.
- [ ] `python -m validation.run` exits 0 (all scenarios pass).
- [ ] Operational Health report shows no unexpected failures for a smoke case.
- [ ] Governance mode configured intentionally (draft vs validated).
- [ ] Backend confirmed (local vs hosted LLM) and credentials/provider
      agreement in place if a hosted backend is enabled.
- [ ] Database file location and backup procedure confirmed.
- [ ] Access controls (reverse proxy / network isolation) in place if non-local.
- [ ] Payer profiles and guideline packs reviewed for the target deployment.
