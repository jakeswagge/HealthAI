# HealthAI — Agent Map

> Documentation only. Describes the AI/agent components and the service-layer
> seam that isolates them.

## The AI isolation seam

All model access goes through `app/services/llm_client.py`:

```
LLMClient (ABC)
  .complete(system, messages, max_tokens, temperature) -> LLMResponse
  .is_ai -> bool
```

Concrete backends (`app/services/`):

| Backend | `is_ai` | Use |
|---------|---------|-----|
| `AnthropicClient` | True | Real Claude (Messages API). Model via `HEALTHAI_CLAUDE_MODEL`, default `claude-opus-4-8`. |
| `LocalHeuristicClient` | False | Offline regex extraction; default when no key. |
| `MockClaudeClient` | True | Scenario-driven test double (valid / invalid_json / markdown / hallucinated / truncated / empty / prose). |

`factory.get_llm_client()` resolution: `HEALTHAI_LLM_BACKEND` override →
`ANTHROPIC_API_KEY` present → Anthropic → else local. `describe_active_backend()`
provides a UI label.

## Agents (each: AI path + deterministic fallback)

### MedicalExtractionAgent — `app/agents/medical_extraction_agent.py`
- **In**: raw document text. **Out**: validated `PatientCase`.
- Prompts via `app/agents/prompts.py`; parses JSON (handles fences/prose),
  validates with pydantic, **retries up to 3×** feeding the error back.
- Offline backend → returns the local heuristic JSON. Confidence falls back to
  completeness when the model omits it.

### GuidelineReviewAgent — `app/review/review_agent.py`
- **In**: `PatientCase` (+ optional document text). **Out**: `ReviewResult`.
- Matches a `ClinicalGuideline` via `GuidelineRepository`. Non-AI backend or
  failure/exhausted-retries → delegates to the deterministic
  `ClinicalReviewEngine` (`app/review/engine.py`). Same JSON contract either way.

### AppealGenerationAgent — `app/appeals/appeal_agent.py`
- **In**: `PatientCase`, `ReviewResult`, optional `ClinicalGuideline`.
  **Out**: `AppealLetter` (structured fields + full letter text).
- Identity fields are sourced from the trusted `PatientCase`, not the model.
  If the model omits `letter_text`, it is rendered deterministically by
  `AppealLetterBuilder` (`app/appeals/builder.py`). Falls back entirely to the
  builder offline / on failure. Safety: never fabricates clinical facts.

### ClaudeEvidenceExtractor — `app/evidence_ai/extractor.py`
- **In**: `CaseDocument` (+ optional OCR text). **Out**: `EvidenceReference[]`.
- Asks Claude for evidence with verbatim quotes; an **anti-fabrication gate**
  drops any item whose quote is not present in the source. Offline / invalid /
  failure → deterministic `EvidenceExtractor` (`app/evidence/extractor.py`).

## Deterministic engines (no model required)

| Engine | Module | Role |
|--------|--------|------|
| `EvidenceExtractor` | `app/evidence/extractor.py` | regex evidence from text/pages |
| `VisionEvidenceExtractor` | `app/vision/extractor.py` | OCR pages → evidence (blends OCR confidence) |
| `CaseAssemblyEngine` | `app/assembly/engine.py` | merge evidence → `UnifiedCaseContext`, detect conflicts |
| `ClinicalReviewEngine` | `app/review/engine.py` | rule-based review |
| `AppealLetterBuilder` | `app/appeals/builder.py` | template appeal letter |
| `EvidenceQualityEngine` | `app/quality/engine.py` | score evidence quality |
| `ConflictResolutionEngine` | `app/resolution/engine.py` | apply human resolutions → authoritative facts |
| `ValidatedEvidenceEngine` | `app/governance/engine.py` | governance filtering |
| `GovernanceComplianceChecker` | `app/governance/compliance.py` | policy violations |
| `DocumentIngestionEngine` | `app/ingestion/engine.py` | detect type + route to OCR/text |

## OCR providers — `app/ocr/providers.py`

`OCRProvider` interface → `OCRPageResult[]`.
- `LocalTesseractOCRProvider` (`is_available` probes pytesseract + binary;
  PDF rasterized via PyMuPDF).
- `MockOCRProvider` (deterministic; decodes bytes as text; default offline).
- `get_ocr_provider()` prefers Tesseract, falls back to mock.

## Retry / fallback pattern (common shape)

```
for attempt in 1..N:
    resp = llm.complete(...)
    try: data = parse_json(resp.text); obj = Model.validate(data); return obj
    except (parse/validation): append corrective message; continue
on LLMError or exhaustion: return deterministic-engine result
```

See `diagrams/agent_components.puml`.
