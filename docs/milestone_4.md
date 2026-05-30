# Milestone 4 - Prior Authorization Appeal Generation

## Scope

Generate a professional prior-authorization appeal letter from the artifacts of
the earlier milestones: a `PatientCase` (M2), a `ReviewResult` (M3), and the
matched `ClinicalGuideline` (M3). The appeal explains why approval should occur,
what evidence supports it, which denial reasons are challenged, and what
additional information may be required.

All previous functionality remains operational and unchanged.

## Architecture

The appeal engine lives in `app/appeals/` and is **independent** of extraction,
review, and OCR.

```
app/
├── appeals/                 # NEW
│   ├── builder.py           # deterministic letter assembly (offline default + fallback)
│   ├── appeal_prompts.py    # Claude prompt engineering (JSON-first)
│   └── appeal_agent.py      # AppealGenerationAgent (Claude + validation + retry)
├── models/
│   └── appeal_letter.py     # NEW: AppealLetter pydantic model
```

### Data flow

```
PatientCase + ReviewResult (+ ClinicalGuideline)
        │
        ▼
AppealGenerationAgent ──(AI backend)──▶ Claude → JSON → validate/retry → AppealLetter
        │
        └─(offline / fallback)──▶ AppealLetterBuilder → AppealLetter
```

## Appeal engine design

### AppealLetter model

Fields: `appeal_id`, `created_at`, `patient_name`, `member_id`,
`insurance_company`, `requested_service`, `original_decision`, `appeal_reason`,
`clinical_summary`, `guideline_support`, `missing_information`,
`recommended_next_steps`, `letter_text`, `confidence_score`. Validators coerce
nulls, clamp confidence, and normalize list fields. Helpers: `summary()`,
`to_txt()`, `to_markdown()`, and a computed `has_letter`.

### AppealGenerationAgent

- **LLM behavior**: Claude Opus via the service layer. Structured JSON first;
  the rendered letter is returned inside `letter_text`. Professional healthcare
  tone, insurance-appeal format. Validates with pydantic and retries up to 3x
  on invalid output.
- **Identity safety**: ids, names, member id, codes, and original decision are
  sourced from the trusted `PatientCase`, not from the model, to prevent drift.
- **Completeness guarantee**: if the model omits `letter_text`, the agent
  renders a complete letter deterministically from the validated fields.
- **Graceful degradation**: when no AI backend is configured, or the AI backend
  fails / exhausts retries, the agent falls back to `AppealLetterBuilder`. The
  `AppealLetter` contract is identical in both modes.

### Letter template

`render_letter_text` produces all required sections, in order: Patient
Information, Clinical Background, Requested Service, Reason For Appeal, Guideline
Support, Missing Evidence, Request For Reconsideration, Signature placeholder.

## Safety

The builder and prompt both enforce: never claim a treatment occurred, a
diagnosis exists, or a test result is available unless it is present in the
inputs. Absent items use the exact phrases **"Documentation was not available"**
or **"Additional clinical evidence may be required"**. Tests assert this wording
and that absent identity/diagnosis are never fabricated.

## Streamlit

A fourth tab, **Appeal Generator**: upload → (cached) extraction → (cached)
review → generate appeal. Displays the appeal summary, confidence score, the
full generated letter, and download buttons for **TXT** and **Markdown**.
Generation is cached in session state and only re-runs on a new document or an
explicit Generate/Regenerate click (see `docs/caching.md`).

## Tests

`test_appeal_model.py`, `test_appeal_builder.py`, `test_appeal_agent.py` cover:
approval / denial / missing-information cases, invalid LLM responses, retry
logic, schema compliance, letter completeness, the no-fabrication safety rules,
and the Humira success criterion.

## Known limitations

- The offline builder is template-based; prose quality is fixed (Claude
  produces richer narrative when configured).
- Guideline content is simplified mock policy, so citations are illustrative.
- The appeal is a drafting aid: it includes a signature placeholder and a
  disclaimer that a provider must review and sign before submission.
- No PDF export yet (TXT + Markdown only).
