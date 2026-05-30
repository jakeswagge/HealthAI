# Architecture Hardening Milestone

Goal: strengthen the existing architecture before adding more features. No new
product features were built (no Appeal Generation, no FastAPI, no OCR engine).

## Task 1 — Realistic LLM testing strategy

- Added `app/services/mock_claude_client.py` with `MockClaudeClient` +
  `MockScenario`.
- It reports `is_ai = True`, so the extraction and review agents take their
  full AI code path (parse → validate → retry) against it.
- Scenarios: `VALID`, `MISSING_FIELDS`, `INVALID_JSON`, `MARKDOWN_JSON`,
  `HALLUCINATED`, `TRUNCATED`, plus `EMPTY` and `PROSE`.
- Scenario lists are consumed one-per-call, making retry sequences trivial to
  script (e.g. `[INVALID_JSON, VALID]`).
- The real Claude implementation (`AnthropicClient`) is unchanged. The
  regex-based `LocalHeuristicClient` remains as the offline default backend but
  is no longer the basis of the agent test doubles.

## Task 2 — Streamlit session-state review

- Added `app/ui/session.py` to centralize caching.
- `st.session_state` stores: extracted text, page count, `PatientCase`,
  extraction metadata, `ReviewResult`, and the review backend flag.
- Documents are keyed by a content signature (`sha256` + name + size). A new
  document clears derived data; the same document preserves it.
- LLM calls occur only on (1) a new upload or (2) explicit Run/Reprocess.
- A single shared uploader (sidebar) is the one source of truth, so the
  multi-tab render model cannot clear the active document.
- Full behavior contract in `docs/caching.md`.

## Task 3 — Large-document protection

- Added `app/extraction/size_validator.py` with `DocumentSizeValidator`.
- Measures page count, character count, and estimated tokens (~4 chars/token).
- Emits advisory warnings when thresholds are exceeded. **No chunking, no RAG** —
  detect and warn only. Surfaced in every UI tab.

## Task 4 — OCR abstraction layer

- Added `app/ocr/` with `OCREngine` interface, `OCRResult`/`OCRCapability`
  types, errors, a `PlaceholderOCREngine` (always unavailable; raises
  `OCRNotAvailableError`), and a `get_ocr_engine` factory.
- **No engine integration**: no Tesseract, Textract, or Claude Vision.

## Task 5 — Environment hardening

- `docs/environment.md`: Python 3.12 supported, 3.13 experimental, dependency
  compatibility considerations, and configuration env vars.
- `requirements.txt` annotated with the same guidance.
- README updated.

## Testing

New test files:
- `test_mock_claude_client.py` — mock behavior + validation/retry/schema
- `test_size_validator.py` — size thresholds and token estimation
- `test_ocr_abstraction.py` — interface + placeholder behavior
- `test_session_state.py` — caching contract (new vs. same doc, reprocess)
- `test_review_agent_mock.py` — review-side validation/retry with the mock

All existing tests continue to pass.
