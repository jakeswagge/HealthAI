# Milestone 3 - Clinical Guideline Decision Engine

## Scope

Determine whether a requested service meets insurance approval criteria, and
explain why approval should occur, why a denial may be justified, what evidence
is missing, and what to do next.

Milestones 1 and 2 remain fully operational and unchanged in behavior.

## Architecture

The review engine is **independent of the extraction engine**.

```
app/
├── guidelines/            # NEW: guideline library (load + match)
│   └── repository.py
├── review/                # NEW: clinical review engine + agent
│   ├── engine.py          # deterministic, rule-based review
│   ├── review_agent.py    # Claude-backed review (AI) w/ retry + fallback
│   ├── review_prompts.py  # review prompt engineering
│   └── evaluation.py      # review evaluation framework
├── models/
│   ├── clinical_guideline.py  # NEW: ClinicalGuideline + criteria
│   └── review_result.py       # NEW: ReviewResult + Recommendation
data/
└── guidelines/            # NEW: guideline JSON library (no DB)
    ├── humira.json
    ├── enbrel.json
    ├── mri_lumbar_spine.json
    ├── ct_chest.json
    └── physical_therapy.json
```

## Data flow

```
PatientCase ─▶ GuidelineRepository.match() ─▶ ClinicalGuideline
                                                   │
                                                   ▼
                         ClinicalReviewEngine / GuidelineReviewAgent
                                                   │
                                                   ▼
                                              ReviewResult
```

## Review engine design

### Evidence model

- The **denial reason** describes a *deficiency* (what the payer found lacking).
- The **diagnosis, requested service, codes, and document text** are
  *supporting* evidence.

For each required criterion:

| Condition | Status |
|-----------|--------|
| Keywords appear in the denial/deficiency text | UNMET |
| Keywords appear in supporting evidence (and not flagged) | MET |
| No evidence either way | UNKNOWN |

### Decision rules

- Any contraindication present → **DENY** (high confidence).
- Any criterion UNMET (explicitly contradicted) → **DENY**.
- All required criteria MET → **APPROVE**.
- Some criteria only UNKNOWN (never contradicted) → **INSUFFICIENT_INFORMATION**.
- No matching guideline → **INSUFFICIENT_INFORMATION** (route to human).

### AI vs. deterministic

`GuidelineReviewAgent` uses the Claude-backed service layer when an API key is
configured (validates JSON output, retries up to 3x). When no AI backend is
present, or the AI backend fails/exhausts retries, it transparently falls back
to the deterministic `ClinicalReviewEngine`. The `ReviewResult` JSON contract
is identical in both modes, so the UI and tests are backend-agnostic.

## Guideline model (`ClinicalGuideline`)

Core fields: `guideline_id`, `service_name`, `diagnosis`, `required_criteria`,
`contraindications`, `supporting_evidence`, `version`, `source`. Optional
matching aids: `aliases`, `applicable_icd10`, `applicable_cpt`.

## ReviewResult model

`recommendation` (APPROVE / DENY / INSUFFICIENT_INFORMATION), `matched_criteria`,
`missing_criteria`, `rationale`, `confidence_score`, plus richer detail:
`missing_evidence`, `recommended_actions`, `contraindications_found`,
`criteria_detail`, `guideline_id`, `service_name`.

## Evaluation

`app/review/evaluation.py` measures recommendation accuracy, JSON validity,
schema compliance, and guideline-matching accuracy over labeled scenarios in
`app/tests/review_scenarios.py` (10 approvals, 10 denials, 5
insufficient-information). All metrics are 1.0 with the deterministic engine.

## Streamlit

A third tab, **Clinical Review**, lets a user upload a document, extract the
patient case, run the review, and view the recommendation, matched/missing
criteria, missing evidence, recommended actions, confidence score, rationale,
and full JSON.

## Known limitations

- Guideline criteria are **simplified mock policies**, not real payer rules.
- The deterministic engine uses keyword matching; nuanced clinical language can
  be missed (the Claude backend mitigates this when configured).
- Only five services have guidelines; unmatched services return
  INSUFFICIENT_INFORMATION for manual review.
- Guidelines are stored as local JSON (no database yet, by design).
