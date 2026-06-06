# HealthAI — Evidence Flow

> Documentation only. Traces how a fact travels from a source document to a
> cited appeal statement, preserving traceability at every hop.

## The evidence contract

`EvidenceReference` (`app/models/evidence_reference.py`) — the live model:

| field | meaning |
|-------|---------|
| `evidence_id` | stable id (`EV-...`) |
| `case_id` | owning case |
| `source_document_id` / `source_filename` | which document |
| `page_number` | 1-indexed source page |
| `section_label` | label/heading the value sat under |
| `quoted_text` | **verbatim** source snippet |
| `normalized_fact` | `"fact_type: value"` |
| `fact_type` | logical field (diagnosis, member_id, ...) |
| `confidence_score` | 0.0–1.0 |

`.citation()` → e.g. `(clinical_note.pdf, p.4)`.

> A parallel `EvidenceReference` also exists in `app/models/evidence.py` (uses
> `field_name`); it belongs to the non-wired alternate lineage. The live flow
> below uses `app/models/evidence_reference.py`.

## Stage-by-stage flow

```
1. INGESTION  (app/ingestion/engine.py)
   bytes + filename -> detect TEXT / SEARCHABLE_PDF / SCANNED_PDF / IMAGE
   - text layer present -> use it (no OCR)
   - else -> OCR provider -> OCRPageResult[] (text + confidence + method)
   -> CaseDocument (raw_text = pages joined by '\f'), persisted

2. EXTRACTION (per document)
   - text path:   EvidenceExtractor.extract(document)        # regex, offline
   - OCR path:    VisionEvidenceExtractor.extract(doc, ocr_pages)
                  (reuses regex per-page logic; confidence = field × OCR)
   - AI path:     ClaudeEvidenceExtractor.extract(document)
                  (Claude + anti-fabrication gate; quote must be in source)
   -> EvidenceReference[]  (each: source doc + page + verbatim quote)

3. ASSEMBLY   (app/assembly/engine.py: CaseAssemblyEngine.assemble)
   - de-duplicate identical facts
   - resolve a "best value" per fact (authoritative doc-type preference, then confidence)
   - detect conflicts (same fact_type, different values) -> ConflictReport (severity)
   - identify missing required fields
   - synthesize PatientCase with per-field FieldSource (doc + page + evidence_id)
   -> UnifiedCaseContext (evidence inventory + resolved facts + conflicts + case)
   Persisted: evidence_references (replace-for-case = idempotent)

4. QUALITY    (app/quality/engine.py: EvidenceQualityEngine.assess_all)
   per reference -> EvidenceQualityAssessment
   (completeness, relevance, consistency, traceability, overall + issues)
   Persisted: evidence_quality

5. REVIEWER WORKBENCH (app/quality/workbench.py)
   per reference: quality + supporting/conflicting siblings + latest decision
   reviewer records APPROVE / REJECT / FLAG -> evidence_review_decisions

6. GOVERNANCE FILTER (app/governance/engine.py: ValidatedEvidenceEngine)
   draft mode -> all evidence; validated mode -> ApprovedEvidenceSet
   (rejected always excluded; below-min-quality excluded; optionally
    unreviewed excluded)

7. LINKING    (app/evidence/linker.py)
   link_review(review, context)  -> ReviewResult.evidence_refs
   link_appeal(appeal, context)  -> AppealLetter.section_evidence (+ unsupported list)
   ids drawn ONLY from the inventory; unsupported items left empty/flagged

8. EXPORT     (app/cases/export.py)
   evidence_inventory.json, traceability_report.md, evidence_quality.json,
   evidence_review_decisions.json, approved_evidence.json, excluded_evidence.json
```

## Traceability guarantees (as implemented)

- **Verbatim quotes only.** `EvidenceExtractor` quotes the matched source line;
  `ClaudeEvidenceExtractor` rejects any quote not found in the source (the
  anti-fabrication gate). Nothing is invented.
- **Source + page preserved** on every reference, through assembly, quality,
  review/appeal linking, and export.
- **OCR uncertainty is visible.** OCR confidence is stored per page and folded
  into evidence confidence; low-confidence pages are flagged + audited.
- **Survives export.** `evidence_inventory.json` + `traceability_report.md`
  carry the full chain; appeal `section_evidence` maps sections → evidence ids.

## "What document/page did this come from?"

`UnifiedCaseContext.patient_case.field_sources[field]` →
`FieldSource(source_document, source_page, evidence_id)`, and the matching
`EvidenceReference.citation()`. The Evidence Explorer and Reviewer Workbench
tabs surface this; exports persist it.

See `diagrams/evidence_flow.puml`.
