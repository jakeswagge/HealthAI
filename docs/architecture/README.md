# HealthAI — Architecture Documentation

> Documentation only. These files describe the **current** architecture as it
> exists in code. No business logic was modified and no code was refactored to
> produce them.

## Contents

| Document | Describes |
|----------|-----------|
| [`system_overview.md`](system_overview.md) | What HealthAI is, design principles, runtime backends, high-level pipeline |
| [`package_map.md`](package_map.md) | Every `app/` package, its modules, and live vs. parallel status |
| [`database_schema.md`](database_schema.md) | All SQLite tables, columns, indexes, and logical relationships |
| [`workflow_map.md`](workflow_map.md) | Case status lifecycle + `CaseService` operations + UI tab mapping |
| [`agent_map.md`](agent_map.md) | AI agents, the LLM service seam, deterministic fallbacks, OCR providers |
| [`evidence_flow.md`](evidence_flow.md) | How a fact travels from source to a cited appeal, with traceability |
| [`governance_flow.md`](governance_flow.md) | Validated-evidence mode, filtering rules, compliance, analytics |

## PlantUML diagrams (`diagrams/`)

| File | Diagram |
|------|---------|
| `package_dependencies.puml` | Package dependency graph |
| `database_schema.puml` | Entity-relationship view of the SQLite schema |
| `workflow_state.puml` | Case status state machine |
| `end_to_end_sequence.puml` | End-to-end sequence (scanned multi-document case) |
| `agent_components.puml` | Agents + the `LLMClient` seam + fallbacks |
| `evidence_flow.puml` | Evidence flow (activity diagram) |
| `governance_flow.puml` | Governance / validated-evidence flow (activity diagram) |

### Rendering the diagrams

The `.puml` files are plain text (PlantUML). Render with any PlantUML tooling,
e.g.:

```bash
# with a local plantuml.jar (requires Java + Graphviz for some diagrams)
java -jar plantuml.jar docs/architecture/diagrams/*.puml

# or the VS Code "PlantUML" extension (Alt+D to preview)
```

No rendering tool is bundled with the project; the source `.puml` files are the
deliverable.

## Accuracy note

The codebase contains two evidence/assembly lineages. The **live** path (wired
into `CaseService` and the Streamlit UI) is documented throughout; the
**parallel** lineage (`app/cases/assembly_service.py`,
`app/cases/evidence_repository.py`, `app/models/evidence.py`,
`app/assembly/traceability.py`) is present but not imported by the live
service/UI. It is flagged where relevant in `package_map.md` and
`evidence_flow.md`.
