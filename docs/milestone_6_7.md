# Milestone 6 & 7 - Multi-Document Case Assembly + Evidence Traceability

## Scope

A case may now contain many supporting documents (denial letter, clinical
notes, referral, lab results, imaging report, prior-auth form). Every extracted
fact, review recommendation, and appeal statement is traceable to a source
document and page. The system can answer: "what document did this come from?",
"what page?", and "what evidence supports this?". All prior functionality
remains operational; model changes are additive and backward-compatible.

## Architecture

New packages, kept separate from extraction / review / appeals / audit:

```
app/
├── evidence/                 # NEW
│   ├── extractor.py          # EvidenceExtractor: text -> EvidenceReference(s)
│   ├── linker.py             # link_review / link_appeal (attach evidence ids)
│   └── repository.py         # EvidenceRepository (SQLite)
├── assembly/                 # NEW
│   └── engine.py             # CaseAssemblyEngine -> UnifiedCaseContext
├── cases/
│   └── document_repository.py# NEW: CaseDocumentRepository (SQLite)
├── models/
│   ├── case_document.py      # NEW: CaseDocument + DocumentCategory + classify
│   ├── evidence_reference.py # NEW: EvidenceReference
│   ├── conflict_report.py    # NEW: ConflictReport / FactConflict / severity
│   └── unified_case_context.py # NEW: UnifiedCaseContext / ResolvedFact
```

Backward-compatible model extensions (all optional, default-empty):
- `PatientCase.field_sources: dict[str, FieldSource]`
- `ReviewResult.evidence_refs: dict[str, list[str]]`
- `AppealLetter.section_evidence: dict[str, list[str]]`

## Schema changes (additive, idempotent)

Two new tables (existing M5 tables untouched):

`case_documents`
| document_id PK, case_id, filename, document_type, uploaded_at, page_count, raw_text |

`evidence_references`
| evidence_id PK, case_id, source_document_id, source_filename, page_number, section_label, quoted_text, normalized_fact, fact_type, confidence_score, created_at |

Indexes on `case_documents.case_id`, `evidence_references.case_id`, and
`evidence_references.source_document_id`. `initialize_schema` creates these
alongside the M5 tables, so existing databases upgrade transparently.

## Evidence architecture

`EvidenceExtractor` scans a `CaseDocument` page-by-page (pages delimited by
`\f` in `raw_text`) and emits an `EvidenceReference` for each fact it can
confidently locate, capturing the 1-indexed page, the section label, a verbatim
quote, a normalized `fact_type: value`, and a confidence score. It never
invents values - no match means no evidence. References are persisted in
`evidence_references` and survive export via `evidence_inventory.json`.

## Assembly architecture

`CaseAssemblyEngine.assemble(case_id, documents)`:
1. Runs the extractor over every document and de-duplicates identical facts.
2. Resolves one "best" value per scalar fact, preferring authoritative document
   types (e.g. diagnosis from a clinical note, denial reason from the denial
   letter), then confidence.
3. Detects conflicts (same fact, different values) with severity:
   - HIGH: member_id, patient_name, date_of_birth, diagnosis
   - MEDIUM: requested_service, denial_reason, insurance_company, decision
   - LOW: physician_name
4. Flags missing required fields (patient_name, member_id, diagnosis,
   requested_service).
5. Synthesizes a backward-compatible `PatientCase` with per-field
   `FieldSource` attribution, so the existing review/appeal engines work
   unchanged.

The result is a `UnifiedCaseContext` (evidence inventory + resolved facts +
conflict report + missing info + synthesized case).

## Traceable review + appeal

`link_review` populates `ReviewResult.evidence_refs` for matched criteria,
missing criteria, denial rationale, and recommendation. `link_appeal` populates
`AppealLetter.section_evidence` per section and returns a list of any
statements that could not be tied to evidence (the quality gate). Linking is
deterministic token-overlap matching against the persisted evidence; it never
fabricates references, and every emitted id exists in the inventory.

## Streamlit

Three new tabs (11 total): **Document Assembly** (attach multiple docs,
auto-classify, assemble), **Evidence Explorer** (browse/filter the inventory,
inspect verbatim source quotes), **Conflict Review** (conflicts with color-coded
severity). The export package gains `evidence_inventory.json`,
`conflict_report.json`, and `traceability_report.md`.

## Tests

New: `test_evidence_extraction.py` (6), `test_case_assembly.py` (12),
`test_evidence_traceability.py` (13) - multi-document, conflicting-document,
missing-information, evidence-traceability, source-attribution, appeal
traceability, case assembly, conflict detection, repositories, and export.
Total suite: **243 passed**.

## Quality guarantees

- Every review recommendation and appeal statement can cite evidence ids;
  `link_appeal` surfaces any unsupported statement.
- No fabricated evidence: extraction and linking only reference text actually
  present in a document.
- Evidence references survive export (`evidence_inventory.json`,
  `traceability_report.md`).

## Known limitations / risks

- Extraction + linking are deterministic/heuristic; nuanced clinical phrasing
  can be missed (a future Claude-backed evidence extractor could improve recall
  while keeping the same `EvidenceReference` contract).
- Page boundaries depend on the `\f` delimiter in `raw_text`; the current TXT
  pipeline treats each upload as one page unless delimiters are present.
- Conflict detection compares normalized string values; semantically-equal but
  differently-worded values may be flagged (false positive) or missed.
- Authoritative-source resolution uses a fixed document-type preference map, not
  recency or document confidence.
