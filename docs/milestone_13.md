# Milestone 13 - Governance-Enforced Reviews + Appeals + Explainability

## Objective

Close the remaining trust gap. Previously an `ApprovedEvidenceSet` existed, but
review and appeal generation were not fully constrained by it. Milestone 13
makes the contract enforceable and auditable:

```
ApprovedEvidenceSet -> Review Agent  -> Review Result   (+ ReviewExplanation)
ApprovedEvidenceSet -> Appeal Agent  -> Appeal Letter   (+ AppealExplanation)
```

In VALIDATED mode the review/appeal are produced from ONLY the
governance-approved evidence. Rejected evidence can never influence the
recommendation, rationale, confidence, or appeal content.

No previous functionality changed. No architecture refactoring, cleanup, or new
infrastructure was introduced.

## Files created

- `app/models/explanation.py` - `EvidenceLineage`, `ReviewExplanation`,
  `AppealExplanation`, `TraceabilityChain`.
- `app/explainability/__init__.py`, `app/explainability/engine.py` -
  `ExplainabilityEngine` (pure, deterministic, offline).
- `app/cases/explainability_service.py` - `ExplainabilityService` +
  `GovernedReview` / `GovernedAppeal` result objects.
- `app/ui/tabs/explainability_tabs.py` - Review/Appeal Explainability tabs.
- `app/tests/test_governance_explainability.py` - 18 tests.

## Files modified

- `app/assembly/engine.py` - added `synthesize_from_evidence(...)` so a
  `PatientCase` can be assembled from a GIVEN evidence list (the approved
  subset) without re-extracting. Shared core `_assemble_from_evidence`; the
  existing `assemble(...)` behavior is unchanged.
- `app/models/audit_event.py` - added `REVIEW_EXPLANATION_GENERATED`,
  `APPEAL_EXPLANATION_GENERATED`.
- `app/models/__init__.py` - export the new explanation models.
- `app/cases/service.py` - facade gains `generate_governed_review`,
  `generate_governed_appeal`, `explain_review`, `explain_appeal`,
  `traceability_chain`, plus the `explainability_engine` attribute and a wired
  `ExplainabilityService`.
- `app/cases/export.py` - `build_export_files` / `build_export_zip` accept and
  emit `review_explanation.json`, `appeal_explanation.json`,
  `traceability_chain.json` (all backward-compatible/optional).
- `app/ui/tabs/case_tabs.py` - export package now includes explanations +
  traceability chain.
- `app/ui/case_ui.py`, `app/ui/dashboard.py` - two new tabs wired in
  (Review Explainability, Appeal Explainability); 19 -> 21 tabs.

## Governance enforcement details

`ExplainabilityService.generate_review` / `generate_appeal`:

1. Build the case's `ApprovedEvidenceSet` via the existing
   `ValidatedEvidenceEngine` (draft -> all; validated -> approved-only,
   quality-gated, rejected-never).
2. Synthesize a `PatientCase` from ONLY the included evidence
   (`assembly.synthesize_from_evidence`). The agents literally never see
   rejected/excluded evidence.
3. Run the review/appeal agent (offline-capable) on that constrained case.
4. Produce an explanation whose `evidence_used` is exactly the included set and
   whose `evidence_excluded` lists the excluded references with reasons.

Reviewer authority always wins: REJECTED evidence is excluded regardless of any
other setting (enforced in the governance engine and re-proven by tests).

## Explainability architecture

- `EvidenceLineage` is one traceability row: evidence id -> source document,
  page, quoted text, reviewer decision, quality score, included flag, and
  (if excluded) the reason.
- `ReviewExplanation` / `AppealExplanation` carry the used/excluded lineage,
  governance mode, confidence, and human-readable reasoning steps.
- `TraceabilityChain` is the full per-case lineage (`included` + `excluded`).
- The engine is pure; the `CaseService` facade records audit events.

## Test results

- New: `app/tests/test_governance_explainability.py` - 18 passed.
- Full suite: **376 passed** (358 prior + 18 new). No regressions; package-level
  architecture cycle tests still pass with the new `app.explainability` package.

Covered: draft uses all evidence; validated excludes rejected; rejected never
influences recommendation/appeal; draft vs validated differ; lineage carries
source + decision + quality; excluded lineage has a reason; used + excluded ==
all (disjoint); exports include the three new files; backward-compatible export
without explanations.

## Example review explanation (validated mode)

A case with a denial letter (diagnosis: Rheumatoid arthritis) and a clinical
note (diagnosis: Osteoarthritis). The reviewer REJECTED the osteoarthritis
evidence.

```json
{
  "review_id": "REV-CASE-...-GL-HUMIRA-001",
  "recommendation": "DENY",
  "governance_mode": "VALIDATED",
  "confidence": 0.8,
  "evidence_used": [
    {"fact_type": "diagnosis", "value": "Rheumatoid arthritis",
     "source_filename": "denial.png", "page_number": 1,
     "reviewer_decision": "PENDING", "quality_score": 0.875, "included": true}
    /* ... patient_name, member_id, requested_service, ... */
  ],
  "evidence_excluded": [
    {"fact_type": "diagnosis", "value": "Osteoarthritis",
     "source_filename": "note.png", "reviewer_decision": "REJECT",
     "included": false, "exclusion_reason": "rejected by reviewer"}
  ],
  "reasoning_steps": [
    "Governance mode VALIDATED: only governance-approved evidence was permitted ...",
    "8 evidence reference(s) used; 1 excluded from influence.",
    "Matched guideline GL-HUMIRA-001 ...",
    "Recommendation DENY at 80% confidence.",
    "Excluded evidence did not contribute to the recommendation, rationale, or confidence."
  ]
}
```

## Example appeal explanation (validated mode)

```
mode: VALIDATED   used: 8   excluded: 1
steps:
 - Governance mode VALIDATED: the appeal was drafted using only governance-approved evidence.
 - 8 evidence reference(s) used; 1 excluded from the appeal.
 - 4 guideline-support point(s) cited.
 - 6 evidence gap(s) disclosed honestly rather than asserted.
 - Appeal confidence 62%.
 - Excluded evidence did not contribute to any appeal statement.
excluded:
 - diagnosis=Osteoarthritis (note.png, p.1) decision=REJECT reason=rejected by reviewer
```

## Remaining risks

- The offline deterministic engine/builder is what runs in tests and without an
  API key; with the Claude backend enabled, the same constrained `PatientCase`
  is passed to the agent, so the governance guarantee (agent only sees approved
  evidence) holds, but free-text generation should still be reviewed by a human
  before external use.
- Explanations reflect the governance settings supplied at generation time;
  changing settings after the fact requires regenerating to refresh the chain.
- The dead parallel evidence lineage noted in Milestone 12 remains (documented
  future work); it is unrelated to this milestone and untouched.
