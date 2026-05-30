# Milestone 10 - Claude Evidence Extraction + Quality Scoring + Reviewer Workbench

## Scope

Improve evidence-extraction quality, score the confidence/quality of evidence,
and give reviewers tools to validate it — all while preserving the existing
`EvidenceReference` contract, traceability, auditability, and reviewer
authority. No fabricated evidence; no unsupported quotes.

## Architecture

New packages (no breaking changes to existing interfaces):

```
app/
├── evidence_ai/              # NEW
│   └── extractor.py          # ClaudeEvidenceExtractor (AI + anti-fabrication gate)
├── quality/                  # NEW
│   ├── engine.py             # EvidenceQualityEngine (scoring + issue detection)
│   ├── repository.py         # EvidenceQualityRepository
│   ├── decision_repository.py# EvidenceReviewDecisionRepository
│   └── workbench.py          # ReviewerWorkbench + EvidenceView
├── models/
│   ├── evidence_quality.py        # NEW: EvidenceQualityAssessment
│   └── evidence_review_decision.py# NEW: EvidenceReviewDecision + EvidenceDecision
```

## Evidence AI architecture

`ClaudeEvidenceExtractor` prompts Claude (via the LLM service layer) for an
`evidence` array where every item carries verbatim `quoted_text`. It then runs
an **anti-fabrication gate**: each item's quote must appear (whitespace/case
tolerant) in the source document, or the item is dropped. The page number is
re-derived from the page the quote actually appears on. Output is ordinary
`EvidenceReference` objects.

Safety + robustness:
- No quote, or a quote not found in the source → the item is rejected (never
  trusted), so nothing is fabricated.
- Offline backend, invalid JSON after retries, or backend failure → graceful
  fallback to the deterministic regex `EvidenceExtractor`.
- Tested with `MockClaudeClient` (valid / markdown / invalid / fabricated).

## Quality engine architecture

`EvidenceQualityEngine.assess_all` scores each reference on four dimensions —
completeness, relevance, consistency, traceability — and a weighted `overall`
(soft-capped by the reference's own confidence). It detects:
- missing support (no quote),
- weak evidence (overall below threshold; value not in quote),
- conflicting support (same fact_type, different values),
- duplicate evidence (same fact_type + value),
- unsupported appeal statements (via the appeal's `section_evidence`).

## Reviewer workbench

`ReviewerWorkbench.build_views` bundles each reference with its quality
assessment, supporting/conflicting siblings, and latest decision.
`record_decision` persists an append-only APPROVE / REJECT / FLAG decision.
`approved_evidence(case_id)` returns evidence that has not been rejected, so
**rejected evidence is excluded from downstream review/appeal** while unreviewed
evidence stays usable — preserving reviewer authority without discarding facts.

## Schema changes (additive, idempotent)

Two new tables; existing tables untouched:

`evidence_quality`
| assessment_id PK, evidence_id, case_id, completeness_score, relevance_score, consistency_score, traceability_score, overall_score, issues_json, timestamp |

`evidence_review_decisions`
| decision_id PK, evidence_id, case_id, reviewer, decision, comments, timestamp |

Indexed on case_id + evidence_id. Created alongside the M5..M9 tables.

## Auditability

`score_evidence` emits `EVIDENCE_QUALITY_SCORED`; each reviewer decision emits
`EVIDENCE_REVIEW_DECISION` (actor = USER). All decisions are append-only.

## Streamlit

Two new tabs (17 total): **Evidence Quality** (score the case, table of
per-dimension scores + issues, weak-evidence warnings) and **Reviewer
Workbench** (per-item source quote, quality, supporting/conflicting evidence,
Approve/Reject/Flag, decision history). Export gains `evidence_quality.json` and
`evidence_review_decisions.json`.

## Tests

`app/tests/test_evidence_quality_workbench.py` (25): schema + backward
compatibility, Claude extraction (mock, markdown, anti-fabrication gate, invalid
fallback, verbatim quotes), quality scoring (conflicting/missing/duplicate/weak/
unsupported-appeal), workbench views + decisions, reject exclusion, audit, and
export. Full suite: **320 passed**.

## Example reviewer workflow

Ingest denial + clinical note + lab → assemble → **Score evidence quality**
(conflicting diagnoses flagged) → open **Reviewer Workbench**, read each source
quote, **Reject** a garbled/low-quality reference (excluded from
`approved_evidence`), **Approve** the rest → review + appeal proceed → export
includes `evidence_quality.json` + `evidence_review_decisions.json`.

## Remaining risks

- The quality engine is heuristic (keyword/overlap based); scores are guidance,
  not ground truth.
- Real Claude extraction is unverified here (no API key); the mock + fallback
  paths are what the tests exercise. The anti-fabrication gate assumes OCR/text
  fidelity — a quote garbled by OCR may fail the gate and be dropped (safe, but
  reduces recall).
- `approved_evidence` currently filters rejected references for downstream use;
  wiring a "use only approved" toggle directly into the review/appeal agents is
  a natural follow-up (the data + helper already exist).
