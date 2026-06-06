# HealthAI — Architecture Review

> Source: analysis of `graphify-out/` (`GRAPH_REPORT.md`, `graph.json` — 1,792
> nodes / 7,631 edges / 42 communities) **cross-checked against the source
> tree**. Graphify reports 71% of edges are `INFERRED` (avg confidence 0.58),
> so every finding below was verified against actual imports, line counts, and
> method counts before being ranked.
>
> **Recommendations only. No code was modified.**

## How to read this

Findings are ranked **Critical / High / Medium / Low** by risk to
maintainability and the project's stated priorities (healthcare quality,
traceability, auditability, reviewer authority). Each finding cites the
evidence (Graphify signal + verified ground truth).

## Severity summary

| # | Finding | Severity |
|---|---------|----------|
| 1 | `cases ↔ analytics` circular dependency (currently masked by a lazy import) | **High** |
| 2 | `CaseService` is a god object (710 lines, 37 methods, 22 wired collaborators) | **High** |
| 3 | `case_ui.py` is oversized (1,001 lines, 15 tab renderers) | **High** |
| 4 | Dead/parallel evidence-assembly lineage still in the tree | **Medium** |
| 5 | `app.models` is an over-coupled hub (49 modules import it; `PatientCase` 245 edges) | **Medium** |
| 6 | `dashboard.py` + `case_ui.py` UI coupling to `CaseService` internals | **Medium** |
| 7 | `export.py` builder accretion (377 lines, one growing function signature) | **Medium** |
| 8 | Duplicated JSON-parse/retry logic across the four agents | **Low** |
| 9 | Graph hygiene: 134 isolated nodes / thin communities (doc-extraction gaps) | **Low** |

No **Critical** findings: there are no runtime-breaking cycles, and the test
suite (346 tests) is green. The High items are structural risks that will slow
future change if left unaddressed.

---

## 1. Circular dependencies — **High**

### Finding
There is exactly one package-level import cycle: **`cases ↔ analytics`**.

- `app/cases/service.py:35` imports `app.analytics.quality_analytics` at module
  top level.
- `app/analytics/quality_analytics.py` imports `app.cases.*` **lazily inside
  `__init__`** with the comment *"Lazy imports avoid a circular dependency."*

Graphify flagged `CaseService` (162 edges, betweenness 0.106) and
`QualityAnalyticsEngine` as cross-community bridges; the verified import graph
confirms the bidirectional pair. No other non-test package pair is bidirectional.

### Why it matters
The cycle is currently *worked around*, not *resolved*. The lazy import is a
latent hazard: any future top-level import of `cases` from `analytics` (or an
eager refactor) will reintroduce an `ImportError`. It also obscures the real
dependency direction.

### Recommendation
Break the cycle structurally rather than with a lazy import:
- Preferred: have `analytics` depend only on the **repositories + storage**
  (`CaseRepository`, `EvidenceQualityRepository`, etc.) and the **models**, not
  on the `cases` package/`CaseService`. Repositories are already the leaf layer.
- Alternatively, extract the small shared pieces analytics needs into a neutral
  module (e.g. `app/storage` or a new `app/readmodels`) that both depend on.
- Keep `CaseService` as the only place that composes `analytics` for the UI.

After the refactor, remove the lazy import so the dependency is explicit and
statically checkable.

---

## 2. `CaseService` god object — **High**

### Finding
`app/cases/service.py` is **710 lines** with **37 public methods** and wires
**22 collaborators** (`self.X = ...`) across **37 distinct `app.*` imports**.
Graphify ranks `CaseService` the #2 god node (162 edges) and a top
betweenness bridge across 7 communities.

It spans: case lifecycle, document ingestion + OCR, assembly, evidence quality,
reviewer decisions, conflict resolution, reviewer feedback, governance,
compliance, analytics, review, appeal, audit, and export.

### Why it matters
It is the single highest-churn file (touched by every milestone), the natural
merge-conflict magnet, and the hardest unit to reason about or test in
isolation. Its breadth is why it bridges so many communities in the graph.

### Recommendation
Keep `CaseService` as a thin **façade**, but delegate to cohesive
sub-services it composes (most engines already exist; this is mostly
re-grouping its methods):
- `CaseLifecycleService` — create/status/human-review/export-marking.
- `IngestionService` — `ingest_document`, OCR persistence (the parallel
  `AssemblyService` already hints at this seam — see Finding 4).
- `EvidenceService` — assemble, score, workbench decisions, approved-evidence.
- `GovernanceService` — settings, validated-evidence set, compliance.
The façade forwards calls; audit logging stays centralized. This shrinks the
class, clarifies service boundaries (Finding 5/6), and makes each area
independently testable. Do it incrementally, one sub-service per PR, with the
existing tests as the safety net.

---

## 3. `case_ui.py` oversized module — **High**

### Finding
`app/ui/case_ui.py` is **1,001 lines** — the largest file in the codebase — and
contains **15 `render_*` tab functions** plus the cached `CaseService` resource
and an inline persistence bridge. `dashboard.py` (645 lines) wires 19 tabs.

### Why it matters
Two thresholds exceeded (>500 lines). All reviewer-facing tabs share one module,
so any tab change risks the others; it is hard to navigate and review.

### Recommendation
Split `case_ui.py` into one module per functional area under `app/ui/tabs/`
(e.g. `evidence_tabs.py`, `governance_tabs.py`, `case_tabs.py`,
`ingestion_tabs.py`), each exporting its `render_*` functions; keep
`dashboard.py` as the only tab-wiring point. Move `get_case_service()` and the
persistence bridge into a small `app/ui/service_access.py`. Pure mechanical
extraction — no behavior change.

---

## 4. Dead / parallel evidence-assembly lineage — **Medium**

### Finding
A second, self-contained evidence/assembly lineage exists but is **not imported
by the live `CaseService`/UI** (verified: `assembly_service` has **0** external
importers):
- `app/cases/assembly_service.py` (`AssemblyService`)
- `app/cases/evidence_repository.py` (imports `app/models/evidence.py`)
- `app/models/evidence.py` (a *second* `EvidenceReference` using `field_name`)
- `app/assembly/traceability.py` (`annotate_review`/`annotate_appeal`)

The live path uses `app/evidence/*`, `app/assembly/engine.py`, and
`app/models/evidence_reference.py`. Graphify lists both `EvidenceReference`
shapes and contributes to the "surprising connections" / isolated-node noise.

### Why it matters
Two models named `EvidenceReference` with different fields (`field_name` vs
`fact_type`) is a correctness trap for future contributors and dilutes the
"single source of truth for traceability" guarantee. It also inflates the graph.

### Recommendation
Decide and converge:
- If the live lineage is canonical (it is), **remove** the parallel modules, or
- If `AssemblyService` is the intended future seam (it aligns with Finding 2),
  migrate the live code onto it and delete the duplicate model.
Either way, end with **one** `EvidenceReference`. (Deletion requires explicit
approval per project rules — flagging, not performing.)

---

## 5. `app.models` over-coupled hub — **Medium**

### Finding
**49 non-test modules import `app.models`**, and the model classes dominate
Graphify's god-node list: `PatientCase` (245 edges, 241 inferred), `ReviewResult`
(146), `AppealLetter` (123), `EvidenceReference` (112), `Decision` (110),
`CaseDocument` (108). `PatientCase` has the highest betweenness (0.139).

### Why it matters
This is largely *healthy* (shared contracts should be widely used), but two
risks: (a) `app/models/__init__.py` is a single broad re-export surface, so a
change there ripples widely; (b) `PatientCase` is accumulating optional
traceability/governance fields (`field_sources`, etc.), trending toward its own
god class.

### Recommendation
- Keep models as the shared leaf, but import from **specific modules**
  (`from app.models.patient_case import PatientCase`) rather than the package
  root to reduce the blast radius of `__init__` changes. (Most code already
  does this; standardize it.)
- Watch `PatientCase` size; if traceability/governance fields keep growing,
  consider a composed `CaseFacts` value object rather than more optional fields.
- These are contracts, not behavior — low urgency, but worth a convention.

---

## 6. UI ↔ `CaseService` internal coupling — **Medium**

### Finding
`case_ui.py` reaches into `CaseService` **attributes** (e.g.
`service.workbench.*`, `service.assembly.assemble(...)`, `service.evidence`,
`service.documents`) rather than only its public methods. Verified in the
export and workbench tab code.

### Why it matters
It couples the UI to `CaseService`'s internal composition, so the Finding-2
refactor would break the UI. It also bypasses the audit-centralization intent.

### Recommendation
Add thin public methods on `CaseService` for everything the UI needs
(`assemble_conflict_report(case_id)`, `evidence_repo` reads, etc.) and have the
UI call only those. Do this **before** the Finding-2 split so the façade is the
stable contract.

---

## 7. `export.py` builder accretion — **Medium**

### Finding
`app/cases/export.py` is **377 lines**; `build_export_files` / `build_export_zip`
have grown to **~14 parameters** each as every milestone appended bundle files
(evidence, conflicts, OCR, quality, decisions, governance, analytics, approved/
excluded evidence).

### Why it matters
Long parallel parameter lists are error-prone (positional drift) and the
function mixes many concerns. Each new artifact widens the signature.

### Recommendation
Introduce an `ExportBundle`/`ExportContext` dataclass that the caller populates,
and register per-artifact "section writers" (`name -> producer`) so adding a
file is appending one writer, not a new parameter. Backward-compatible shim can
keep the current signature during migration.

---

## 8. Duplicated agent JSON-parse/retry logic — **Low**

### Finding
`MedicalExtractionAgent`, `GuidelineReviewAgent`, `AppealGenerationAgent`, and
`ClaudeEvidenceExtractor` each implement the same pattern: call `LLMClient`,
extract a JSON object (direct / fenced / embedded), validate with pydantic,
retry with a corrective message, fall back to a deterministic engine. Graphify
clusters these in adjacent communities (0, 2, 8, 9, 13, 14) with a shared shape.

### Why it matters
Four near-identical `_extract_json_object`/retry implementations drift over time;
a fix to JSON-repair logic must be made in four places.

### Recommendation
Extract a shared `app/services/json_agent.py` helper (e.g.
`parse_json_object(text)` + a `run_with_retry(client, system, messages, validate,
fallback)` utility). Each agent keeps its prompt + schema; the plumbing is
shared. This also strengthens the AI-isolation seam.

---

## 9. Graph hygiene / documentation gaps — **Low**

### Finding
Graphify reports **134 isolated nodes** (≤1 connection) and many "thin
communities" (size 1–2), mostly `__init__.py` package docstrings, validator
methods, and property docstrings. This is a documentation-extraction artifact
(71% inferred edges), not a code defect.

### Why it matters
Low signal-to-noise in the graph makes future Graphify analyses harder to read;
it does not affect runtime.

### Recommendation
Optional: nothing structural required. If cleaner graphs are desired, the
recently added `docs/architecture/*` (overview, package map, flows) provide the
explicit edges Graphify infers with low confidence — re-running Graphify with
those docs included should raise extraction quality.

---

## Service boundaries (current, as verified)

The codebase already has clean **engine** seams behind `CaseService`:

| Boundary | Modules | Cohesion (Graphify) |
|----------|---------|---------------------|
| LLM access | `app/services/*` (`LLMClient` + backends) | strong (isolation seam) |
| OCR / ingestion | `app/ocr`, `app/ingestion`, `app/vision` | C10 0.08, C4 |
| Evidence + assembly | `app/evidence`, `app/assembly` | C8 0.06, C11 0.08 |
| Quality + workbench | `app/quality` | C13 0.17 |
| Review | `app/review` | C2 area |
| Appeals | `app/appeals` | C0 0.02 (large) |
| Resolution / governance | `app/resolution`, `app/governance` | dedicated |
| Analytics / metrics | `app/analytics`, `app/metrics` | C1 0.05 |
| Persistence | `app/storage` + repositories | leaf |

The boundaries are sound; the issues are the **orchestrator (`CaseService`)**
and **UI** straddling all of them, plus the one cycle.

## Future microservice candidates

This is a local, single-process app; microservices are **not** recommended now.
If a future deployment needs them, the cleanest extraction order (driven by
cohesion + a stable contract) is:

1. **OCR / Ingestion service** — `ocr` + `ingestion` + `vision`. Self-contained,
   IO-heavy, independently scalable, clear `bytes -> OCRPageResult[]` contract.
   Lowest coupling to the rest. *Best first candidate.*
2. **Evidence + Assembly service** — `evidence` + `evidence_ai` + `assembly`.
   Contract: `documents -> UnifiedCaseContext`. Requires consolidating the
   duplicate `EvidenceReference` (Finding 4) first.
3. **LLM gateway** — `app/services` already is a clean seam; could become a
   shared internal API (extraction/review/appeal prompts as endpoints).
4. **Review/Appeal engine service** — depends on guidelines + LLM gateway.

**Not candidates:** `cases`/`audit`/`storage`/`governance` should remain a single
**case-management core** — they share the SQLite transactional boundary and the
audit trail, which must stay co-located for auditability/traceability guarantees.

## Suggested sequencing (lowest risk first)

1. Finding 6 (UI → façade methods) — unblocks safe refactors.
2. Finding 1 (break `cases ↔ analytics` cycle via repositories).
3. Finding 4 (converge/remove the duplicate evidence lineage — needs approval).
4. Finding 3 (split `case_ui.py` by tab area).
5. Finding 2 (decompose `CaseService` into sub-services).
6. Findings 7, 8 (export bundle + shared agent JSON helper).
7. Finding 5, 9 (conventions + graph hygiene) — ongoing.

All are **recommendations**; none were applied.
