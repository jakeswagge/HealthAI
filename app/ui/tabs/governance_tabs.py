"""Governance tabs: Governance Settings (M11) and Quality Analytics (M11).

Moved verbatim from the old ``case_ui.py`` during the Milestone 12 UI split.
"""

from __future__ import annotations

import streamlit as st

from app.models.governance import GovernanceSettings
from app.ui.tabs.common import get_case_service


# --------------------------------------------------------------------------- #
# Tab: Governance Settings (Milestone 11)
# --------------------------------------------------------------------------- #
def render_governance_settings_tab() -> None:
    st.caption(
        "Configure how strictly evidence must be validated before review and "
        "appeal generation use it. Reviewer authority always wins: rejected "
        "evidence is never used in validated mode."
    )

    service = get_case_service()
    current = service.get_governance_settings()

    with st.form(key="governance_form"):
        validated = st.toggle(
            "Validated evidence mode",
            value=current.validated_evidence_mode,
            help="When on, downstream review/appeal use only governance-filtered evidence.",
        )
        allow_unreviewed = st.toggle(
            "Allow unreviewed evidence (validated mode)",
            value=current.allow_unreviewed_evidence,
            help="If off, only reviewer-APPROVED evidence is used in validated mode.",
        )
        min_quality = st.slider(
            "Minimum evidence quality score", 0.0, 1.0,
            float(current.minimum_quality_score), 0.05,
        )
        require_conflict = st.toggle(
            "Require conflict resolution", value=current.require_conflict_resolution
        )
        require_human = st.toggle(
            "Require human review before export",
            value=current.require_human_review_before_export,
        )
        reviewer = st.text_input("Your name (for the audit trail)", value="admin")
        submitted = st.form_submit_button("Save governance settings", type="primary")

    if submitted:
        service.update_governance_settings(
            GovernanceSettings(
                validated_evidence_mode=validated,
                allow_unreviewed_evidence=allow_unreviewed,
                minimum_quality_score=min_quality,
                require_conflict_resolution=require_conflict,
                require_human_review_before_export=require_human,
            ),
            actor=reviewer.strip() or "admin",
        )
        st.success("Governance settings saved (audited).")

    mode = "VALIDATED" if current.validated_evidence_mode else "DRAFT"
    st.info(f"Current mode: **{mode}**")

    # Per-case governance compliance + filtered evidence preview.
    cases = [c.case_id for c in service.list_cases()]
    if cases:
        st.markdown("#### Per-case governance compliance")
        selected = st.selectbox("Case", cases, key="gov_case_select")
        used, aset = service.evidence_for_consumption(selected)
        report = service.check_compliance(selected)
        c1, c2, c3 = st.columns(3)
        c1.metric("Mode", aset.mode.value)
        c2.metric("Included evidence", aset.included_count)
        c3.metric("Excluded evidence", aset.excluded_count)

        if report.is_compliant:
            st.success("Governance compliance: PASS")
        else:
            st.error(f"Governance compliance: {len(report.violations)} violation(s)")
            for v in report.violations:
                st.markdown(f"- **{v.code}** ({v.severity}): {v.description}")

        if aset.excluded:
            st.markdown("##### Excluded evidence")
            st.dataframe(
                [
                    {"fact": e.fact_type, "value": e.value, "reason": e.reason}
                    for e in aset.excluded
                ],
                use_container_width=True,
                hide_index=True,
            )


# --------------------------------------------------------------------------- #
# Tab: Quality Analytics (Milestone 11)
# --------------------------------------------------------------------------- #
def render_quality_analytics_tab() -> None:
    st.caption(
        "Organization-wide evidence-quality and workflow analytics, computed "
        "on demand from local storage."
    )

    service = get_case_service()
    a = service.quality_analytics()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cases", a.total_cases)
    c2.metric("Evidence", a.total_evidence)
    c3.metric("Decisions", a.evidence_decisions)
    c4.metric("Avg quality", f"{a.average_quality_score:.0%}")

    c5, c6, c7 = st.columns(3)
    c5.metric("Approval rate", f"{a.evidence_approval_rate:.0%}")
    c6.metric("Rejection rate", f"{a.evidence_rejection_rate:.0%}")
    c7.metric("Flag rate", f"{a.evidence_flag_rate:.0%}")

    c8, c9, c10 = st.columns(3)
    c8.metric("Weak evidence rate", f"{a.weak_evidence_rate:.0%}")
    c9.metric("Conflict rate", f"{a.conflict_rate:.0%}")
    c10.metric("Appeal success rate", f"{a.appeal_generation_success_rate:.0%}")

    st.metric("Avg review turnaround (s)", a.review_turnaround_seconds)

    st.markdown("#### Evidence decision mix")
    mix = {
        "approved": a.evidence_approval_rate,
        "rejected": a.evidence_rejection_rate,
        "flagged": a.evidence_flag_rate,
    }
    if a.evidence_decisions:
        st.bar_chart(mix)
    else:
        st.caption("No reviewer decisions recorded yet.")

    with st.expander("Raw analytics JSON"):
        st.json(a.as_dict())
