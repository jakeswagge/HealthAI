"""Operations tabs: Payer Management, Operational Health, Validation Runner.

Final Milestone. Read-only operator inspectors built on the CaseService facade:
- Payer Management: browse payer profiles, run a payer-pack-aware review and
  compare guideline-pack outcomes for a case.
- Operational Health: local diagnostics (failures, fallback rate, conflicts).
- Validation Runner: run the bundled mock scenarios and show pass/fail.

No proprietary payer content; all packs are simplified mock policies.
"""

from __future__ import annotations

import streamlit as st

from app.models.governance import GovernanceSettings
from app.ui.tabs.common import get_case_service


def _cases_with_evidence(service) -> list[str]:
    return [
        c.case_id for c in service.list_cases()
        if service.evidence.count_for_case(c.case_id) > 0
    ]


# --------------------------------------------------------------------------- #
# Tab: Payer Management (Final Milestone)
# --------------------------------------------------------------------------- #
def render_payer_management_tab() -> None:
    st.caption(
        "Configure which payer policy governs a review. Guideline packs are "
        "simplified mock policies (no proprietary content). The same case can "
        "be reviewed under different payers to compare guideline-pack outcomes."
    )

    service = get_case_service()
    payers = service.list_payers()

    st.markdown(f"### Payer profiles ({len(payers)})")
    st.dataframe(
        [
            {
                "payer_id": p.payer_id,
                "name": p.payer_name,
                "pack": p.guideline_pack,
                "version": p.version,
                "effective": p.effective_date or "—",
                "status": p.status.value,
            }
            for p in payers
        ],
        use_container_width=True,
        hide_index=True,
    )

    cases = _cases_with_evidence(service)
    if not cases:
        st.info("No assembled evidence yet. Assemble + score a case first.")
        return

    st.markdown("### Review a case under a payer")
    selected = st.selectbox("Case", cases, key="payer_case_select")
    payer_ids = [p.payer_id for p in payers]
    chosen = st.multiselect(
        "Payers to compare", payer_ids, default=payer_ids[: min(3, len(payer_ids))],
        key="payer_compare_select",
    )

    if st.button("Run payer comparison", type="primary", key="payer_run"):
        settings = service.get_governance_settings()
        rows = []
        for pid in chosen:
            pr = service.review_with_payer(selected, pid, settings)
            r = pr.review
            rows.append(
                {
                    "payer": pr.payer.payer_id,
                    "pack": r.guideline_pack or "—",
                    "version": r.guideline_version or "—",
                    "recommendation": r.recommendation.value,
                    "confidence": f"{r.confidence_score:.0%}",
                    "matched": len(r.matched_criteria),
                    "missing": len(r.missing_criteria),
                }
            )
        st.markdown("#### Guideline-pack comparison")
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption(
            "Differences reflect each pack's required criteria. Governance "
            "enforcement and explainability are preserved for every payer."
        )


# --------------------------------------------------------------------------- #
# Tab: Operational Health (Final Milestone)
# --------------------------------------------------------------------------- #
def render_operational_health_tab() -> None:
    st.caption(
        "Local operational diagnostics derived from the audit trail. No "
        "external observability platform; everything is computed on demand."
    )

    service = get_case_service()
    report = service.operational_health()

    status = "🟢 HEALTHY" if report.is_healthy else "🟠 ATTENTION"
    st.markdown(f"### System status: {status}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cases", report.total_cases)
    c2.metric("Documents", report.total_documents)
    c3.metric("Total failures", report.total_failures)
    c4.metric("Conflicts", report.conflicts_detected)

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("OCR failures", report.ocr_failures)
    c6.metric("Review failures", report.review_failures)
    c7.metric("Appeal failures", report.appeal_failures)
    c8.metric("AI fallbacks", report.claude_fallbacks)

    c9, c10 = st.columns(2)
    c9.metric("AI fallback rate", f"{report.claude_fallback_rate:.0%}")
    c10.metric("Conflict frequency", f"{report.conflict_frequency:.0%}")
    c11, c12 = st.columns(2)
    c11.metric("Governance violations", report.governance_violations)
    c12.metric("Extraction failures", report.extraction_failures)

    if report.warnings:
        st.markdown("#### Warnings")
        for w in report.warnings:
            st.warning(w)
    else:
        st.success("No operational warnings.")

    with st.expander("Raw operational health JSON"):
        st.json(report.as_dict())


# --------------------------------------------------------------------------- #
# Tab: Validation Runner (Final Milestone)
# --------------------------------------------------------------------------- #
def render_validation_runner_tab() -> None:
    st.caption(
        "Run the bundled mock payer scenarios (denial + approval) through the "
        "full pipeline and check expected outcomes. Synthetic data only - no "
        "PHI, no proprietary content."
    )

    if not st.button("Run validation suite", type="primary", key="validation_run"):
        st.info("Click to run the validation scenarios.")
        return

    # Import lazily so the UI module stays light.
    from app.validation.runner import ValidationRunner

    runner = ValidationRunner(settings=GovernanceSettings())
    report = runner.run()

    c1, c2, c3 = st.columns(3)
    c1.metric("Checks", report.total)
    c2.metric("Passed", report.passed)
    c3.metric("Pass rate", f"{report.pass_rate:.0%}")

    if report.all_passed:
        st.success("All validation scenarios passed.")
    else:
        st.error(f"{report.failed} validation check(s) failed.")

    st.dataframe(
        [r.as_dict() for r in report.results],
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Raw validation report JSON"):
        st.json(report.as_dict())
