"""Explainability tabs: Review Explainability + Appeal Explainability (M13).

These tabs let reviewers inspect, for a selected case:
- the governance mode in force and its impact on evidence selection,
- the evidence lineage actually used by the generated review/appeal,
- the evidence excluded by governance (with reasons), proving that rejected
  evidence never influenced the output.

They are read-only inspectors built on the governance-enforced generation in
``CaseService`` (``generate_governed_review`` / ``generate_governed_appeal``).
No review/appeal behavior is changed here.
"""

from __future__ import annotations

import streamlit as st

from app.appeals.appeal_agent import AppealAgentError
from app.ui.tabs.common import get_case_service


def _lineage_rows(links) -> list[dict]:
    return [
        {
            "evidence_id": link.evidence_id,
            "fact": link.fact_type or "—",
            "value": link.value,
            "source": link.source_filename or link.source_document_id or "—",
            "page": link.page_number if link.page_number is not None else "—",
            "reviewer": link.reviewer_decision,
            "quality": (
                f"{link.quality_score:.0%}" if link.quality_score is not None else "—"
            ),
            "reason": link.exclusion_reason or "",
        }
        for link in links
    ]


def _cases_with_evidence(service) -> list[str]:
    return [
        c.case_id for c in service.list_cases()
        if service.evidence.count_for_case(c.case_id) > 0
    ]


# --------------------------------------------------------------------------- #
# Tab: Review Explainability (Milestone 13)
# --------------------------------------------------------------------------- #
def render_review_explainability_tab() -> None:
    st.caption(
        "Inspect why a review reached its recommendation and exactly which "
        "evidence influenced it. In VALIDATED mode only governance-approved "
        "evidence is used; rejected/excluded evidence is shown separately and "
        "never influences the outcome."
    )

    service = get_case_service()
    cases = _cases_with_evidence(service)
    if not cases:
        st.info("No assembled evidence yet. Assemble a case in Document Assembly.")
        return

    selected = st.selectbox("Case", cases, key="rx_case_select")
    settings = service.get_governance_settings()
    mode = "VALIDATED" if settings.validated_evidence_mode else "DRAFT"
    st.info(f"Active governance mode: **{mode}**")

    if not st.button("Generate governed review", type="primary", key="rx_run"):
        st.caption(
            "Click to run a governance-enforced review and build its "
            "explainability chain."
        )
        return

    governed = service.generate_governed_review(selected, settings)
    exp = governed.explanation

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Recommendation", exp.recommendation)
    c2.metric("Mode", exp.governance_mode.value)
    c3.metric("Confidence", f"{exp.confidence:.0%}")
    c4.metric("Evidence used", len(exp.evidence_used))

    st.markdown("#### Reasoning steps")
    for step in exp.reasoning_steps:
        st.markdown(f"- {step}")

    st.markdown(f"#### Evidence used ({len(exp.evidence_used)})")
    if exp.evidence_used:
        st.dataframe(_lineage_rows(exp.evidence_used), use_container_width=True, hide_index=True)
    else:
        st.caption("No evidence was permitted for this review.")

    st.markdown(f"#### Evidence excluded by governance ({len(exp.evidence_excluded)})")
    if exp.evidence_excluded:
        st.dataframe(_lineage_rows(exp.evidence_excluded), use_container_width=True, hide_index=True)
        st.caption(
            "Excluded evidence did not influence the recommendation, rationale, "
            "or confidence."
        )
    else:
        st.caption("No evidence was excluded under the current governance mode.")

    with st.expander("Review explanation JSON"):
        st.json(exp.model_dump(mode="json"))


# --------------------------------------------------------------------------- #
# Tab: Appeal Explainability (Milestone 13)
# --------------------------------------------------------------------------- #
def render_appeal_explainability_tab() -> None:
    st.caption(
        "Inspect which evidence backs each appeal and which evidence governance "
        "excluded. In VALIDATED mode rejected/excluded evidence never appears in "
        "any appeal statement."
    )

    service = get_case_service()
    cases = _cases_with_evidence(service)
    if not cases:
        st.info("No assembled evidence yet. Assemble a case in Document Assembly.")
        return

    selected = st.selectbox("Case", cases, key="ax_case_select")
    settings = service.get_governance_settings()
    mode = "VALIDATED" if settings.validated_evidence_mode else "DRAFT"
    st.info(f"Active governance mode: **{mode}**")

    if not st.button("Generate governed appeal", type="primary", key="ax_run"):
        st.caption(
            "Click to run a governance-enforced appeal and build its "
            "explainability chain."
        )
        return

    try:
        governed = service.generate_governed_appeal(selected, settings)
    except AppealAgentError:
        st.info(
            "ℹ️ Appeal Explainability Unavailable: No active insurance denial exists for this case file. Explainability logs are only generated for formal payer denials."
        )
        return
    exp = governed.explanation

    c1, c2, c3 = st.columns(3)
    c1.metric("Mode", exp.governance_mode.value)
    c2.metric("Confidence", f"{exp.confidence:.0%}")
    c3.metric("Evidence used", len(exp.evidence_used))

    st.markdown("#### Reasoning steps")
    for step in exp.reasoning_steps:
        st.markdown(f"- {step}")

    if exp.guideline_support:
        st.markdown("#### Guideline support")
        for item in exp.guideline_support:
            st.markdown(f"- {item}")

    if exp.missing_evidence:
        st.markdown("#### Missing evidence (disclosed, not asserted)")
        for item in exp.missing_evidence:
            st.markdown(f"- {item}")

    st.markdown(f"#### Evidence used ({len(exp.evidence_used)})")
    if exp.evidence_used:
        st.dataframe(_lineage_rows(exp.evidence_used), use_container_width=True, hide_index=True)
    else:
        st.caption("No evidence was permitted for this appeal.")

    st.markdown(f"#### Evidence excluded by governance ({len(exp.evidence_excluded)})")
    if exp.evidence_excluded:
        st.dataframe(_lineage_rows(exp.evidence_excluded), use_container_width=True, hide_index=True)
        st.caption("Excluded evidence did not contribute to any appeal statement.")
    else:
        st.caption("No evidence was excluded under the current governance mode.")

    st.markdown("#### Generated appeal letter")
    with st.expander("Letter text"):
        st.markdown(governed.appeal.letter_text)

    with st.expander("Appeal explanation JSON"):
        st.json(exp.model_dump(mode="json"))
