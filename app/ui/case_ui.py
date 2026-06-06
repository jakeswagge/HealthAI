"""Backward-compatible facade for the Streamlit case-management tabs.

Milestone 12 (architecture stabilization) split the original ~1000-line
``case_ui.py`` into cohesive modules under :mod:`app.ui.tabs`. This module is
kept as a thin re-export shim so existing importers - notably
``app/ui/dashboard.py`` - continue to work unchanged. Behavior is identical; the
``render_*`` functions, shared helpers, and module-level constants all live in
``app.ui.tabs`` now and are re-exported here.

No new behavior was added. See :mod:`app.ui.tabs` for the per-domain modules.
"""

from __future__ import annotations

# Shared service access + persistence bridge.
from app.ui.tabs.common import (
    get_case_service,
    get_metrics_collector,
    persist_current_case,
    select_or_create_case,
    _select_or_create_case,
)

# Case lifecycle tabs.
from app.ui.tabs.case_tabs import (
    render_case_management_tab,
    render_human_review_tab,
    render_audit_log_tab,
    render_metrics_tab,
    _render_case_detail,
)

# Ingestion + OCR tabs.
from app.ui.tabs.ingestion_tabs import (
    render_document_ingestion_tab,
    render_ocr_explorer_tab,
    _INGEST_TYPES,
)

# Assembly / evidence / conflict-review tabs.
from app.ui.tabs.assembly_tabs import (
    render_document_assembly_tab,
    render_evidence_explorer_tab,
    render_conflict_review_tab,
    _SEVERITY_RENDER,
)

# Evidence quality + reviewer workbench tabs.
from app.ui.tabs.evidence_quality_tabs import (
    render_evidence_quality_tab,
    render_reviewer_workbench_tab,
    _record_ev_decision,
    _EV_DECISION_RENDER,
)

# Conflict resolution + reviewer feedback tabs.
from app.ui.tabs.resolution_tabs import (
    render_conflict_resolution_tab,
    render_reviewer_feedback_tab,
    _strip_citation,
)

# Governance + analytics tabs.
from app.ui.tabs.governance_tabs import (
    render_governance_settings_tab,
    render_quality_analytics_tab,
)

# Explainability tabs (Milestone 13).
from app.ui.tabs.explainability_tabs import (
    render_review_explainability_tab,
    render_appeal_explainability_tab,
)

# Operations tabs (Final Milestone): payer mgmt, operational health, validation.
from app.ui.tabs.operations_tabs import (
    render_payer_management_tab,
    render_operational_health_tab,
    render_validation_runner_tab,
)

__all__ = [
    # Shared helpers.
    "get_case_service",
    "get_metrics_collector",
    "persist_current_case",
    "select_or_create_case",
    # Case lifecycle.
    "render_case_management_tab",
    "render_human_review_tab",
    "render_audit_log_tab",
    "render_metrics_tab",
    # Ingestion + OCR.
    "render_document_ingestion_tab",
    "render_ocr_explorer_tab",
    # Assembly / evidence / conflicts.
    "render_document_assembly_tab",
    "render_evidence_explorer_tab",
    "render_conflict_review_tab",
    # Evidence quality + workbench.
    "render_evidence_quality_tab",
    "render_reviewer_workbench_tab",
    # Resolution + feedback.
    "render_conflict_resolution_tab",
    "render_reviewer_feedback_tab",
    # Governance + analytics.
    "render_governance_settings_tab",
    "render_quality_analytics_tab",
    # Explainability (Milestone 13).
    "render_review_explainability_tab",
    "render_appeal_explainability_tab",
    # Operations (Final Milestone).
    "render_payer_management_tab",
    "render_operational_health_tab",
    "render_validation_runner_tab",
]
