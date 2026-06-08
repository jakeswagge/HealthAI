# Remaining Fixes

## Objective

This document lists the remaining work needed to make HealthAI stable, predictable, and easier to use. It focuses on real defects, operational gaps, and architectural cleanup, not wishlist features.

## Highest Priority

### 1. Finish the deterministic extraction and review hardening

Recent fixes improved several denial-review edge cases, but this area is still the highest-risk part of the product because small parsing mistakes can produce unsafe review outcomes.

Remaining work:

- Add more exact-file regression tests for denial letters with:
  - policy boilerplate
  - educational drug language
  - missing-documentation phrasing
  - shorthand or malformed field labels
  - contradictory diagnosis wording
- Continue hardening `LocalHeuristicClient` against extraction bleed.
- Continue hardening `ClinicalReviewEngine` against false-positive criterion matches.
- Verify that denial-context, educational-context, and negated-context handling are consistently applied across diagnosis, TB, specialist, and step-therapy criteria.

### 2. Make OCR behavior understandable in the UI

The OCR feature is implemented, but the current UX is misleading.

Current problem:

- OCR Explorer only shows persisted OCR results.
- TXT and searchable PDFs bypass OCR by design.
- Users can upload documents and still see "No OCR results yet," which looks broken even when the app is behaving correctly.

Required fixes:

- Rename or reposition OCR Explorer so it reads as an inspection surface, not an action surface.
- Show why no OCR rows exist:
  - `TXT files do not use OCR`
  - `This PDF had a text layer, so OCR was skipped`
  - `Real OCR unavailable because Tesseract is not installed`
- Show OCR status next to each uploaded document:
  - `OCR used`
  - `Text layer used`
  - `OCR unavailable`
- Prevent the UI from implying OCR should work on unsupported/non-OCR paths.

### 3. Install or bundle real OCR if scanned document support matters

Right now the environment is using `MockOCRProvider`, not real Tesseract OCR.

This is acceptable for tests, but not for real scanned-document use.

Required fixes:

- Install and validate Tesseract-based OCR in the target runtime.
- Add an app-level readiness check for OCR dependencies.
- Surface a clear warning in the UI when real OCR is unavailable.
- Decide whether desktop packaging will bundle OCR dependencies or require separate installation.

## Product and Workflow Fixes

### 4. Redesign the UI around workflow, not tabs

The current UI is functionally rich but too fragmented.

Required fixes:

- Reduce navigation sprawl.
- Center the primary workflow around:
  - documents
  - extraction
  - review
  - appeal
  - operations
- Make case selection persistent and obvious.
- Move OCR details into the document workflow instead of a detached viewer-only tab.
- Improve empty states and system status messaging.

### 5. Clarify ingestion vs assembly vs review states

Users should be able to tell what has and has not happened to a case.

Required fixes:

- Show whether a document was:
  - uploaded only
  - ingested
  - OCR-processed
  - assembled into evidence
  - reviewed
- Make required next actions explicit.
- Remove ambiguous UI states where a case appears present but incomplete.

### 6. Fix the remaining failing test

Current known failure:

- `app/tests/test_patient_case_model.py::TestDerived::test_summary_for_pending_is_neutral`

Required fix:

- Align `PatientCase.summary()` behavior with the expected neutral pending wording, or update the test only if behavior was intentionally changed and all downstream uses agree.

This should be resolved so the suite is fully green.

## Architecture Cleanup

### 7. Continue shrinking `CaseService` responsibility

`CaseService` was decomposed, but it remains a central coupling point.

Required fixes:

- Reduce cross-domain knowledge in the facade.
- Make sub-services more independently testable.
- Limit UI code from reaching into service internals and repository internals.

### 8. Remove dead or parallel module lineage

The existing architecture docs already note dead or parallel assembly/traceability lineage.

Required fixes:

- Identify modules with zero live importers.
- Remove or consolidate them.
- Update tests and docs to reflect the single intended path.

### 9. Reduce duplicated rule logic

Some rule vocabulary and context handling are duplicated across deterministic paths.

Required fixes:

- Consolidate repeated negation/absence/policy-text logic where practical.
- Keep behavior deterministic and test-backed.
- Avoid broad refactors that destabilize the current safety fixes.

## Persistence and Packaging Fixes

### 10. Move SQLite storage out of the repo for real app usage

Current default DB path:

- `data/healthai.db`

This is workable in development but not ideal for a packaged desktop app.

Required fixes:

- Move runtime DB storage to per-user app-data locations:
  - Windows: `%APPDATA%/HealthAI/`
  - macOS: `~/Library/Application Support/HealthAI/`
- Keep dev/test overrides supported.
- Update docs and startup logic accordingly.

### 11. Handle process lifecycle and DB locking more cleanly

Current issue class:

- Streamlit/Python processes can keep the SQLite file locked.

Required fixes:

- Ensure app shutdown closes DB connections cleanly.
- Reduce accidental multi-process contention.
- Make reset/debug workflows safer and more explicit.

### 12. Decide desktop-app packaging strategy

If the goal is Windows/macOS desktop distribution, this needs an explicit path.

Required work:

- Choose between:
  - packaged Streamlit wrapper
  - Electron/Tauri wrapper
  - deeper UI rewrite later
- Define how OCR, SQLite, and guideline data ship with the app.
- Define whether code signing/notarization is required.

## Quality and Release Fixes

### 13. Keep expanding exact-file regression coverage

The project benefits from fixture-based tests that mirror real denial text.

Required fixes:

- Add more real-format regressions around:
  - OCR noise
  - payer boilerplate
  - denial rationale variations
  - partial approvals
  - missing evidence statements
  - unsupported indication language

### 14. Validate current docs against the code

The repo has multiple architecture and milestone docs. Some are now historical rather than operational.

Required fixes:

- Keep one current architecture document authoritative.
- Mark older milestone docs as historical where appropriate.
- Ensure UI/OCR behavior described in docs matches actual runtime behavior.

### 15. Improve operational diagnostics in the UI

The app already computes operational health, but key runtime constraints are still too hidden.

Required fixes:

- Show active OCR provider clearly.
- Show whether AI backends are active or deterministic mode is in use.
- Show current DB path.
- Show whether a case has OCR rows, evidence rows, review results, and governance outputs.

## Definition of "Bug Free Enough"

This project does not need perfection to be usable, but it should meet these minimum conditions:

1. Full test suite green.
2. No known unsafe deterministic review false positives for the covered scenarios.
3. No misleading OCR/UI states for common document uploads.
4. Real scanned-document OCR either works or is clearly disabled.
5. Core workflows are understandable without reading internal docs.
6. DB lifecycle is predictable and safe for normal use.

## Suggested Execution Order

1. Fix the remaining `PatientCase.summary()` failure.
2. Redesign the OCR/document workflow UI.
3. Enable or explicitly gate real OCR.
4. Continue deterministic denial-language regression hardening.
5. Simplify the main UI layout.
6. Clean up architectural debt and dead lineage.
7. Move runtime persistence to app-data paths.
8. Decide desktop packaging path.

## Summary

The main remaining work is not adding more core features. The main remaining work is making the existing system easier to trust, easier to operate, and harder to confuse with misleading UI states or edge-case parsing errors.
