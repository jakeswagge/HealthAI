# Caching & LLM-Call Behavior

This document is the contract for when HealthAI performs expensive work
(text extraction and, most importantly, **Claude/LLM calls**) in the Streamlit
UI.

## Why this matters

Streamlit re-runs the entire script on every interaction — button clicks,
widget changes, and **tab switches**. Without caching, simply switching from
the "Structured Extraction" tab to "Clinical Review" would re-trigger Claude
calls, wasting tokens, money, and time. All caching lives in
`app/ui/session.py`.

## What is stored in `st.session_state`

| Key                  | Contents                                             |
|----------------------|------------------------------------------------------|
| `doc_signature`      | Content signature of the active document             |
| `doc_filename`       | Original filename                                    |
| `doc_text`           | Extracted raw text (Milestone 1)                     |
| `doc_page_count`     | Page count                                           |
| `patient_case`       | Structured `PatientCase` (Milestone 2)               |
| `extraction_meta`    | Attempts / backend / repaired flag                   |
| `review_result`      | `ReviewResult` (Milestone 3)                         |
| `review_used_ai`     | Whether the review used Claude or the rule engine    |

## Document signature

A document is identified by `sha256(bytes)` combined with its name and size
(`document_signature`). This is how we detect a "new document": the signature
changes, and all derived data (text, case, review) is cleared.

## When LLM calls happen

LLM-backed work runs **only** when:

1. **A new document is uploaded** — the signature changes; the next time a tab
   needs a case/review, the agent runs once and the result is cached.
2. **The user clicks an explicit button** — "Run structured extraction",
   "Run clinical review", or "Reprocess".

LLM calls **never** happen on:

- **Tab switches** — cached `patient_case` / `review_result` are reused.
- **Reruns from unrelated widget interactions** — same cached values.
- **Re-displaying results** — rendering reads from session state only.

### Single shared uploader

The file uploader is rendered **once** in the sidebar and synced a single time
per rerun. This avoids a subtle bug: `st.tabs` renders every tab body on every
run, so per-tab uploaders would each see an empty value in the inactive tabs
and clear the active document. One uploader = one source of truth.

## Reprocessing

Each AI tab has a **Reprocess** button. It calls
`session.invalidate_case_and_review()`, which clears the cached case and review
(but keeps the already-extracted raw text) so the agents run fresh. This is the
only user-driven path that re-invokes Claude for an unchanged document.

## Extraction stages and their cost

| Stage                | Cost      | Cached? | Re-runs when…                          |
|----------------------|-----------|---------|----------------------------------------|
| Raw text extraction  | cheap     | yes     | new document only                      |
| Structured extraction| LLM call  | yes     | new document, Run, or Reprocess        |
| Clinical review      | LLM call  | yes     | new document, Run, or Reprocess        |

## Tests

`app/tests/test_session_state.py` verifies: new documents clear the cache, the
same document preserves it (the tab-switch case), and reprocess invalidates the
case/review while keeping the raw text.
