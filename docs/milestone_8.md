# Milestone 8 - Human Conflict Resolution + Reviewer Feedback Learning

## Scope

Let human reviewers resolve conflicting evidence and establish an authoritative
case record, capture those decisions for audit, and collect structured reviewer
feedback as learning data. No automatic conflict resolution; nothing is
overwritten without audit history; rejected values and original evidence are
always preserved. No machine learning is performed.

## Architecture

New packages, separate from extraction / review / appeals / assembly:

```
app/
├── resolution/                 # NEW
│   ├── repository.py           # ConflictResolutionRepository, AuthoritativeFactRepository
│   └── engine.py               # ConflictResolutionEngine
├── feedback/                   # NEW
│   ├── repository.py           # ReviewerFeedbackRepository
│   └── dataset.py              # FeedbackDataset (export-only)
├── models/
│   ├── conflict_resolution.py  # NEW: ConflictResolution, AuthoritativeFact, ResolutionSource
│   └── reviewer_feedback.py    # NEW: ReviewerFeedback, FeedbackTarget, FeedbackVerdict
```

`CaseService` gains the repositories + a `ConflictResolutionEngine` and methods:
`resolve_conflict`, `list_resolutions`, `list_authoritative_facts`,
`authoritative_patient_case`, `record_reviewer_feedback`, `list_feedback`.

## Schema changes (additive, idempotent)

Three new tables; existing tables untouched:

`conflict_resolutions`
| resolution_id PK, case_id, conflict_id, fact_type, chosen_value, rejected_values_json, reviewer_name, justification, timestamp |

`authoritative_facts` (UNIQUE(case_id, fact_type))
| fact_id PK, case_id, fact_type, value, source_document, source_page, resolution_source, confidence, resolution_id, updated_at |

`reviewer_feedback`
| feedback_id PK, case_id, reviewer, target_type, target_id, feedback, comments, timestamp |

A stable `conflict_id` (`CFL-<case_id>-<fact_type>`) was added to `FactConflict`
(default-empty, populated by the assembly engine) so resolutions can reference a
conflict across reruns. `initialize_schema` creates the new tables alongside the
M5/6/7 tables; older databases upgrade transparently.

## Resolution architecture

`ConflictResolutionEngine`:
- `seed_system_facts(context)` — writes SYSTEM authoritative facts from the
  auto-resolved values, but never overrides an existing HUMAN fact.
- `resolve(...)` — records an append-only `ConflictResolution` (preserving the
  rejected values), upserts a HUMAN `AuthoritativeFact`, and emits two audit
  events (`CONFLICT_RESOLVED`, `AUTHORITATIVE_FACT_UPDATED`). Requires a chosen
  value and a reviewer name; raises otherwise. No automatic resolution.
- `apply_to_case(case, case_id)` — returns a copy of the `PatientCase` with
  authoritative facts applied (scalar fields), each marked with a `FieldSource`.

`assemble_case` now seeds SYSTEM facts and applies any HUMAN facts to the stored
case, so re-assembly never clobbers a human decision.

## Case impact

After a resolution, the stored `PatientCase` and `authoritative_patient_case()`
reflect the human-chosen value. Review and appeal generation run on that
authoritative case, and the export bundle includes the authoritative facts. A
test confirms re-assembly preserves the HUMAN fact.

## Feedback architecture

`ReviewerFeedback` records a verdict (CORRECT / INCORRECT / PARTIAL) against a
target stage (EXTRACTION / REVIEW / APPEAL / ASSEMBLY). `FeedbackDataset`
aggregates feedback + resolutions + authoritative facts into an exportable JSON
learning dataset. It is data collection only — no retraining, no ML.

## Auditability

Every resolution emits `CONFLICT_RESOLVED` + `AUTHORITATIVE_FACT_UPDATED`; every
feedback action emits `REVIEWER_FEEDBACK_RECORDED`. Resolutions are append-only,
so reversing a decision keeps both entries in history.

## Streamlit

Two new tabs (13 total): **Conflict Resolution** (select authoritative value,
enter justification, submit; shows current authoritative facts + resolution
history) and **Reviewer Feedback** (rate each stage, view history, download the
learning dataset). The export package gains `authoritative_facts.json`,
`conflict_resolutions.json`, `reviewer_feedback.json`.

## Tests

`app/tests/test_conflict_resolution.py` (20): schema + backward compatibility,
SYSTEM seeding, human resolution, rejected-value preservation, append-only
history, validation guards, case impact, re-assembly preservation, feedback,
audit, export, and the learning dataset. Full suite: **263 passed**.

## Quality requirements met

- No automatic conflict resolution — `resolve` only runs on explicit human input.
- Original evidence preserved (evidence store is untouched by resolution).
- Rejected values preserved in `conflict_resolutions`.
- Nothing overwritten without audit history (append-only resolutions + audit
  events; authoritative upsert is always backed by a recorded resolution).

## Known limitations / risks

- Authoritative facts apply to scalar `PatientCase` fields; code lists keep
  their assembled union (no per-code resolution yet).
- Conflict ids are deterministic per (case, fact_type); if assembly later
  detects a different conflict shape for the same fact, the id is reused.
- The learning dataset is collected but never used programmatically (by design).
- No authentication, so "reviewer_name" is self-asserted (out of scope, as in M5).
