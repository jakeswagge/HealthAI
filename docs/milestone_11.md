# Milestone 11 - Validated Evidence Mode + Reviewer Governance + Quality Analytics

## Scope

Let organizations operate HealthAI on reviewer-approved evidence only
(validated mode), provide governance controls, and surface evidence-quality and
workflow analytics. Reviewer authority always wins, all filtering is auditable,
and governance decisions are traceable. No breaking changes to existing models.

## Architecture

New packages (separate from extraction/review/appeals):

```
app/
├── governance/                 # NEW
│   ├── repository.py           # GovernanceSettingsRepository (single global row)
│   ├── engine.py               # ValidatedEvidenceEngine (filtering)
│   └── compliance.py           # GovernanceComplianceChecker
├── analytics/                  # NEW
│   └── quality_analytics.py    # QualityAnalyticsEngine + QualityAnalytics
├── models/
│   └── governance.py           # NEW: GovernanceSettings, ApprovedEvidenceSet,
│                               #      GovernanceComplianceReport, EvidenceMode, ...
```

## Schema changes (additive, idempotent)

One new table; existing tables untouched:

`governance_settings` (single global row, id = "GLOBAL")
| settings_id PK, validated_evidence_mode, allow_unreviewed_evidence, minimum_quality_score, require_conflict_resolution, require_human_review_before_export, updated_at |

## Governance architecture

`GovernanceSettings` knobs: `validated_evidence_mode`,
`allow_unreviewed_evidence`, `minimum_quality_score`,
`require_conflict_resolution`, `require_human_review_before_export`.

`ValidatedEvidenceEngine.build_set` produces an `ApprovedEvidenceSet` (included
ids + excluded items with reasons). Filtering rules in validated mode:
1. **Rejected evidence is always excluded** (reviewer authority wins).
2. Evidence below `minimum_quality_score` is excluded.
3. If `allow_unreviewed_evidence` is False, only APPROVED evidence is included.
Draft mode includes everything (current behavior preserved).

`CaseService.evidence_for_consumption(case_id)` returns the
`(filtered_evidence, ApprovedEvidenceSet)` downstream review/appeal should use,
and audits the application (`VALIDATED_EVIDENCE_APPLIED`). Settings changes are
audited (`GOVERNANCE_SETTINGS_UPDATED`); compliance runs are audited
(`COMPLIANCE_CHECK_RUN`).

## Compliance architecture

`GovernanceComplianceChecker.check` detects:
- `APPEAL_WITH_WEAK_EVIDENCE` (appeal generated while weak evidence is used),
- `UNRESOLVED_CONFLICTS` (when policy requires resolution),
- `EXPORT_WITHOUT_HUMAN_REVIEW` (when policy requires it),
- `LOW_QUALITY_EVIDENCE_PRESENT` (below the minimum quality score).
Returns a `GovernanceComplianceReport` with per-violation severity.

## Analytics architecture

`QualityAnalyticsEngine.collect` computes (read-only, on demand): evidence
approval / rejection / flag rates, average quality score, weak-evidence rate,
conflict rate, review turnaround time (created → human-review completed), and
appeal-generation success rate. Lazy repository imports avoid a circular
dependency with the cases package.

## Streamlit

Two new tabs (19 total): **Governance Settings** (toggle validated mode, set
thresholds, save with audit; per-case compliance + excluded-evidence preview)
and **Quality Analytics** (rates, averages, decision mix, turnaround). The
export package gains `governance_report.json`, `quality_analytics.json`,
`approved_evidence.json`, `excluded_evidence.json`.

## Tests

`app/tests/test_governance_analytics.py` (26): schema + backward compatibility,
settings persistence + audit, draft vs validated filtering, rejected-always-
excluded, approval/quality gates, compliance violations (unresolved conflicts,
export-without-review, low-quality), analytics rates, and export. Full suite:
**346 passed**.

## Example validated-evidence workflow

Ingest denial + note + lab → assemble → score quality → reviewer APPROVE one /
REJECT one / FLAG one → **Draft mode** uses all 10 evidence refs; enable
**Validated mode** → uses 9 (the rejected ref excluded with reason "rejected by
reviewer") → compliance flags the unresolved diagnosis conflict (HIGH) →
analytics shows 33% approval/rejection/flag, 100% conflict rate → export
includes governance + analytics + approved/excluded evidence files.

## Example compliance report (validated, strict policy)

```json
{
  "case_id": "CASE-...",
  "mode": "VALIDATED",
  "violations": [
    {"code": "UNRESOLVED_CONFLICTS", "severity": "HIGH",
     "description": "Unresolved conflicts remain for: diagnosis", "evidence_ids": []}
  ]
}
```

## Quality requirements met

- Reviewer authority always wins: rejected evidence is never included in
  validated mode (filtering rule #1, with a dedicated test).
- All filtering is auditable (`VALIDATED_EVIDENCE_APPLIED`), as are settings
  changes and compliance checks.
- Governance decisions are traceable (audit events + exported governance report
  + approved/excluded evidence files).

## Remaining risks
- Review/appeal **agents** still operate from the assembled `PatientCase`;
  `evidence_for_consumption` provides the governance-filtered set and the
  exports/compliance reflect it, but wiring the filtered set directly into the
  agent prompts (so generated text only cites included evidence) is a natural
  follow-up.
- Governance settings are a single global policy (no per-payer / per-org
  scoping yet).
- Analytics recompute conflict rate by re-assembling each case on demand; fine
  for local scale but O(cases) per call.
