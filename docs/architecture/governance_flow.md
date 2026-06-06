# HealthAI — Governance Flow

> Documentation only. Describes validated-evidence mode, the compliance checks,
> and how reviewer authority is enforced.

## Settings

`GovernanceSettings` (`app/models/governance.py`), persisted as a single global
row in `governance_settings` (id `"GLOBAL"`):

| setting | effect |
|---------|--------|
| `validated_evidence_mode` | False → DRAFT (all evidence); True → VALIDATED (filtered) |
| `allow_unreviewed_evidence` | in validated mode, whether undecided evidence is allowed |
| `minimum_quality_score` | evidence below this overall quality is excluded |
| `require_conflict_resolution` | unresolved conflicts → compliance violation |
| `require_human_review_before_export` | export without human review → violation |

`GovernanceSettings.mode` → `EvidenceMode.DRAFT | VALIDATED`. Changes go through
`CaseService.update_governance_settings` and record a
`GOVERNANCE_SETTINGS_UPDATED` audit event.

## Validated-evidence filtering

`ValidatedEvidenceEngine.build_set(case_id, evidence, settings, approved_ids,
rejected_ids, quality_by_id)` → `ApprovedEvidenceSet` (included ids + excluded
items with reasons).

**Draft mode**: every reference is included.

**Validated mode** — applied per reference, in order:
1. **REJECTED → excluded** ("rejected by reviewer"). *Reviewer authority wins;
   this rule is unconditional and runs first.*
2. **Below `minimum_quality_score` → excluded** ("quality X below minimum Y").
3. **If `allow_unreviewed_evidence` is False and not APPROVED → excluded**
   ("not approved by a reviewer").
4. Otherwise included.

`CaseService.evidence_for_consumption(case_id)` returns
`(filtered_evidence, ApprovedEvidenceSet)` for downstream consumers and records
a `VALIDATED_EVIDENCE_APPLIED` audit event. Rejected evidence can never appear
in the validated set (rule 1, covered by a dedicated test).

```
evidence ──> ValidatedEvidenceEngine.build_set(settings,
                approved_ids, rejected_ids, quality_by_id)
          ──> ApprovedEvidenceSet { included_ids, excluded[reason], mode }
          ──> filter_evidence() ──> evidence for review / appeal / export
```

## Compliance checks

`GovernanceComplianceChecker.check(...)` → `GovernanceComplianceReport`
(`violations[]` with severity). Detects:

| code | severity | condition |
|------|----------|-----------|
| `APPEAL_WITH_WEAK_EVIDENCE` | HIGH | appeal exists while weak evidence is used |
| `UNRESOLVED_CONFLICTS` | HIGH | `require_conflict_resolution` and unresolved conflicts remain |
| `EXPORT_WITHOUT_HUMAN_REVIEW` | HIGH | `require_human_review_before_export`, exported, no human review |
| `LOW_QUALITY_EVIDENCE_PRESENT` | MEDIUM | usable evidence below `minimum_quality_score` |

`CaseService.check_compliance` computes inputs (appeal/human-review/export
presence, quality, unresolved conflicts via re-assembly vs. resolutions, and the
governance-filtered usable evidence ids), runs the checker, and records a
`COMPLIANCE_CHECK_RUN` audit event.

## Analytics

`QualityAnalyticsEngine.collect()` → `QualityAnalytics`: evidence approval /
rejection / flag rates, average quality, weak-evidence rate, conflict rate,
review turnaround (created → human-review-completed), appeal success rate.
Read-only, on demand, lazy repository imports.

## How reviewer authority + auditability are enforced

- **Rejected evidence excluded first** in validated mode (unconditional).
- **Authoritative facts**: human conflict resolutions (`HUMAN` source) override
  SYSTEM auto-resolution and are re-applied on re-assembly (never clobbered).
- **Every governance action is audited**: settings update, validated-evidence
  application, and compliance run all write `AuditEvent`s.
- **Exports carry the proof**: `governance_report.json`, `quality_analytics.json`,
  `approved_evidence.json`, `excluded_evidence.json` (with exclusion reasons).

## Draft vs. validated (example)

```
3 docs, 10 evidence refs; reviewer rejects 1.
DRAFT:     evidence_for_consumption -> 10 used
VALIDATED: evidence_for_consumption -> 9 used  (rejected excluded, reason recorded)
compliance (strict policy): UNRESOLVED_CONFLICTS (HIGH) if diagnosis conflict unresolved
```

See `diagrams/governance_flow.puml`.
