# Milestone 9 - OCR + Intelligent Document Ingestion + Vision-Based Evidence

## Scope

HealthAI can now ingest scanned PDFs, faxes, and images (PNG/JPG/JPEG),
detect whether a text layer already exists, run OCR only when needed, and feed
OCR-derived evidence into the existing EvidenceReference → UnifiedCaseContext →
review → appeal → audit pipeline with full traceability. No text or evidence is
fabricated; OCR uncertainty is always visible.

## Architecture

New packages (existing interfaces preserved):

```
app/
├── ocr/
│   ├── providers.py      # NEW: OCRProvider, LocalTesseractOCRProvider, MockOCRProvider
│   └── repository.py     # NEW: OCRResultRepository (SQLite)
├── ingestion/            # NEW
│   ├── engine.py         # DocumentIngestionEngine (detect + route)
│   └── classifier.py     # DocumentClassifier (auto + manual override)
├── vision/               # NEW
│   └── extractor.py      # VisionEvidenceExtractor (OCR pages -> EvidenceReference)
├── models/
│   └── ocr_result.py     # NEW: OCRPageResult + ProcessingMethod
```

The original `app/ocr/base.py` (`OCREngine`, `OCRResult`, `PlaceholderOCREngine`)
is untouched, so the hardening-milestone abstraction and its tests still pass.

## OCR architecture

`OCRProvider` is a page-level interface returning `OCRPageResult` objects
(`document_id`, `page_number`, `raw_text`, `confidence`, `processing_method`,
`timestamp`).

- `LocalTesseractOCRProvider` — real offline OCR via `pytesseract` (+ Pillow,
  and PyMuPDF for PDF rasterization). All heavy imports are lazy; `is_available`
  probes for the bindings and the tesseract binary. When unavailable it raises
  `OCRNotAvailableError` instead of crashing. Per-page confidence is derived
  from Tesseract's per-word `conf` scores.
- `MockOCRProvider` — deterministic, dependency-free provider that decodes the
  input bytes as text (multi-page via the `\f` delimiter). It is the offline
  default so the entire pipeline and the test suite run without Tesseract.
- `get_ocr_provider()` returns Tesseract if available, else the mock.

## Ingestion architecture

`DocumentIngestionEngine.detect_kind` classifies an upload as
`TEXT / SEARCHABLE_PDF / SCANNED_PDF / IMAGE / UNSUPPORTED`. PDFs are probed for
a usable embedded text layer (≥ 20 non-whitespace chars) — searchable PDFs use
their text layer directly (no OCR), scanned/image PDFs and images go through
OCR. `ingest()` returns an `IngestionResult` with per-page text, the OCR
results, the classified `DocumentCategory`, `ocr_used`, `ocr_available`, and
warnings. Unsupported types and unavailable OCR degrade gracefully (warning +
empty text, never a crash, never fabricated text).

## Classification architecture

`DocumentClassifier` wraps the existing keyword heuristic
(`classify_document`) and adds manual override. Classes: `DENIAL_LETTER`,
`CLINICAL_NOTE`, `REFERRAL`, `LAB_RESULT`, `IMAGING_REPORT`, `PRIOR_AUTH_FORM`,
`OTHER`.

## Vision extraction architecture

`VisionEvidenceExtractor` reuses the deterministic per-page field detection of
the text `EvidenceExtractor` (no logic duplication) on OCR pages, and blends OCR
confidence into each reference (`final = field_confidence * ocr_confidence`) so
low-quality OCR yields low-confidence evidence. Output is ordinary
`EvidenceReference` objects, so OCR-derived facts participate in conflict
detection, assembly, authoritative facts, and reviewer resolution unchanged.

## Schema changes (additive, idempotent)

One new table; existing tables untouched:

`ocr_results`
| ocr_id PK, case_id, document_id, page_number, raw_text, confidence, processing_method, timestamp |

Indexed on `case_id` and `document_id`. `CaseService.ingest_document` persists
documents (page text joined by `\f`) + their OCR page results and records audit
events.

## Quality gates

- OCR confidence threshold is configurable (default 60%); per-page confidence is
  stored and surfaced.
- Low-confidence pages are flagged in the UI (red) and recorded as audit
  warnings — never silently accepted.
- Reviewers can read the exact OCR source text per page in the OCR Explorer.
- OCR unavailable → warning + empty text, workflow continues.

## Streamlit

Two new tabs (15 total): **Document Ingestion** (upload images/scanned PDFs,
choose/override type, set confidence threshold, see kind + method + warnings)
and **OCR Explorer** (page-by-page OCR text, confidence, processing method,
classified type, low-confidence flags). Export gains `ocr_results.json`,
`document_classification.json`, `ocr_traceability_report.md`.

## Tests

`app/tests/test_ocr_ingestion.py` (32): OCR providers (mock + tesseract-absent
path), scanned vs searchable PDF detection, image/multi-page routing, graceful
degradation, classification (+override), vision evidence + confidence blending,
service ingestion/persistence/audit, the 3-scanned-document success criterion,
traceability, and export. Full suite: **295 passed**.

## OCR accuracy limitations

- The environment here has no Tesseract binary, so real OCR was exercised via
  graceful-degradation tests; the deterministic mock provider drives the
  pipeline. Real-world OCR accuracy depends on scan quality, DPI, and language
  packs and will be lower than the mock's perfect decode.
- Confidence is a mean of Tesseract per-word scores — a heuristic, not a
  guarantee of correctness.
- No image pre-processing (deskew/denoise/binarize) yet; poor scans may need it.
- Handwriting is declared as a future capability but not implemented.

## Example scanned-document workflow

Ingest `scanned_denial.png` + `clinical_note.jpg` + `lab_report.jpeg` →
detected as IMAGE, OCR'd (per-page confidence stored), classified
DENIAL_LETTER / CLINICAL_NOTE / LAB_RESULT → assembled into evidence with source
+ page → diagnosis conflict (RA vs Osteoarthritis) detected HIGH → review (DENY)
+ appeal generated → export includes `ocr_results.json`,
`document_classification.json`, `ocr_traceability_report.md`.

## Remaining risks

- Real OCR is unverified in this environment (no tesseract binary); only the
  graceful-degradation and mock paths are exercised by tests.
- Mock OCR's perfect decode can mask issues that only appear with noisy real
  OCR (e.g. mis-split fields).
- Image pre-processing and language configuration are not implemented.
- The text-layer threshold (20 chars) is heuristic; an unusual searchable PDF
  with very little text could be mis-detected as scanned.
