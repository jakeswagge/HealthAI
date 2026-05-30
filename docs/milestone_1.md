# Milestone 1 - Document Extraction

## Scope

Milestone 1 delivers a runnable local application that:

- Serves a Streamlit dashboard.
- Accepts **PDF** and **TXT** uploads.
- Extracts raw text from the uploaded document.
- Displays the extracted text and basic metadata in the browser.

### Explicitly out of scope (future milestones)

- AI / LLM-based extraction
- Clinical guideline matching
- Appeal generation
- OCR for scanned/image-only PDFs

## Architecture

```
streamlit_app.py            # entry point -> renders the dashboard
app/
├── ui/dashboard.py         # Streamlit UI: upload, display, metadata
├── extraction/
│   ├── validation.py       # filename/extension validation
│   └── extractor.py        # TXT + PDF text extraction (PyMuPDF)
├── models/document.py      # pydantic models (ExtractedDocument, DocumentType)
└── tests/                  # pytest suite
data/sample_docs/           # mock healthcare documents
```

## Data flow

1. User uploads a file in the Streamlit UI.
2. `validation.get_document_type()` confirms the extension is supported.
3. `extractor.extract_text_from_bytes()` routes to the TXT or PDF extractor.
4. Result is wrapped in an `ExtractedDocument` (pydantic) and rendered.
