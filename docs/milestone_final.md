# Final Milestone - Payer Guideline Packs + Operational Hardening + Production Readiness

## Objective

Make the platform configurable for different payer policies, increase
operational safety, and prepare for real-world validation. All previous
functionality remains operational; no architecture refactoring, no major UI
redesign, no deployment work.

## Part 1 - Payer guideline packs

### Files created
- `app/models/payer.py` - `PayerProfile` (`payer_id`, `payer_name`,
  `guideline_pack`, `version`, `effective_date`, `status`), `PayerStatus`,
  `KNOWN_PACKS`.
- `app/payers/__init__.py`, `app/payers/repository.py` (`PayerRepository`),
  `app/payers/packs.py` (`GuidelinePackResolver`).
- `app/cases/payer_service.py` - `PayerService` + `PayerReview`/`PayerAppeal`.
- `data/payers/*.json` - DEFAULT, AETNA, UNITEDHEALTHCARE, CIGNA, HUMANA,
  MOCK_PAYER profiles.
- `data/guideline_packs/<PACK>/humira.json` - simplified MOCK overlays for each
  pack (no proprietary content).

### Architecture
```
Case -> Payer Selection -> Guideline Pack -> Review/Appeal
```
A "pack" is the base guideline library (`data/guidelines/`) with optional
pack-specific overrides overlaid from `data/guideline_packs/<PACK>/`, matched by
`guideline_id`. `PayerService` resolves the pack for a payer, builds a
`GuidelineReviewAgent`/`AppealGenerationAgent` bound to that pack's repository,
and runs the existing governance-enforced, explainable pipeline. Results are
stamped with `payer_id`, `guideline_pack`, and `guideline_version` (new optional
fields on `ReviewResult` and `AppealLetter`; stored as JSON, no schema change).

Governance enforcement and explainability are fully preserved: a payer review
still runs on the governance-approved evidence subset, and rejected evidence
never influences the result (covered by a dedicated test).

## Part 2 - Operational hardening

### Files created
- `app/models/operational_health.py` - `OperationalHealthReport`.
- `app/operations/__init__.py`, `app/operations/health.py` -
  `OperationalHealthMonitor`.

### Architecture
The monitor derives signals from the local audit trail (OCR failures,
extraction/review/appeal failures, Claude fallbacks) plus conflict frequency
(via the assembly engine) and optional governance violations (via an injected
compliance callable). Detection is decoupled from agent internals by scanning
stable audit-detail markers, so no agent behavior changed. Everything is local
and on demand; no external observability platform.

## Part 3 - Validation datasets + runner

### Files created
- `validation/__init__.py`, `validation/run.py` (CLI),
  `validation/datasets/scenarios.json` (mock denial + approval + pack-divergence
  scenarios; synthetic, no PHI).
- `app/validation/__init__.py`, `app/validation/runner.py` - `ValidationRunner`,
  `ValidationReport`, `ValidationResult`.

### Architecture
The runner spins up an isolated in-memory `CaseService`, ingests each scenario's
documents, assembles + scores evidence, runs a payer-pack-aware governed review
per payer, and checks each scenario's expectations. `python -m validation.run`
prints a summary and exits non-zero on failure (CI-gateable).

## Streamlit
Three new tabs (21 total): **Payer Management** (browse profiles, compare
guideline-pack outcomes for a case), **Operational Health** (local
diagnostics), **Validation Runner** (run scenarios, show pass/fail).
`case_ui.py` re-exports the new render functions; `dashboard.py` wires them in.

## Exports
`build_export_files` / `build_export_zip` now also emit (optional,
backward-compatible): `payer_profile.json`, `operational_health.json`,
`validation_report.json`. The Case Management export bundle includes the payer
profile and operational health automatically.

## Test results
- New: `app/tests/test_payer_packs_operations.py` - 26 passed.
- Full suite: **402 passed** (376 prior + 26 new). Architecture cycle/boundary
  tests updated to recognize `app.validation` as an application-level harness
  (alongside `app.ui`/`app.cases`) permitted to use the facade; no package
  cycles introduced by `app.payers`, `app.operations`, or `app.validation`.

## Example payer comparison (same case, RA Humira denial)

| Payer | Pack | Version | Recommendation | Matched | Missing |
| --- | --- | --- | --- | --- | --- |
| DEFAULT | DEFAULT | 2026.1 | DENY | 1 | 3 |
| AETNA | AETNA | AETNA-2026.1 | DENY | 1 | 4 |
| UNITEDHEALTHCARE | UNITEDHEALTHCARE | UHC-2026.1 | DENY | 1 | 2 |

Same input, pack-aware output: Aetna's stricter pack (dual-DMARD + disease
activity) raises the missing-criteria count; UHC's leaner pack lowers it. A
diagnosis-only MOCK_PAYER pack approves a complete-diagnosis approval scenario
where stricter packs return INSUFFICIENT_INFORMATION.

## Operational architecture summary
- Local-only diagnostics; derived on demand from SQLite.
- Tracks OCR/extraction/review/appeal failures, Claude fallback rate, governance
  violations, conflict frequency; surfaces human-readable warnings + a coarse
  `is_healthy` flag.

## Validation architecture summary
- Declarative JSON scenarios (documents + payers + per-payer expectations).
- Deterministic offline runner; supports exact `recommendation` or
  `recommendation_in` set matching.
- CLI + UI + tests all exercise it; all bundled scenarios pass.

## Production readiness summary
See `docs/production_readiness.md`: configuration, backups, database management,
security assumptions, PHI handling, operational limitations, human-review
requirements, and a pre-launch checklist. HealthAI remains decision-support;
a qualified human must review every output before external use.

## Remaining risks
- Guideline packs are simplified mock policies, not real payer medical policy.
- Single-node SQLite; not for concurrent multi-writer or high-throughput use.
- No built-in authN/Z or transport security; must run local or behind an
  authenticating proxy.
- With the Claude backend enabled, document text is sent to Anthropic; operators
  own the BAA/compliance configuration. The local backend keeps all data on-host.
- The dead parallel evidence lineage from Milestone 12 remains (documented
  future work; untouched).
