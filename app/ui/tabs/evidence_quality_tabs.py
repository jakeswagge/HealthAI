"""Evidence-quality tabs: Evidence Quality (M10) and Reviewer Workbench (M10).

Moved verbatim from the old ``case_ui.py`` during the Milestone 12 UI split.
"""

from __future__ import annotations

import streamlit as st

from app.models.evidence_review_decision import EvidenceDecision
from app.ui.tabs.common import get_case_service


# --------------------------------------------------------------------------- #
# Tab: Evidence Quality (Milestone 10)
# --------------------------------------------------------------------------- #
def render_evidence_quality_tab() -> None:
    st.caption(
        "Score the quality of extracted evidence (completeness, relevance, "
        "consistency, traceability) and surface weak or conflicting evidence. "
        "Optionally re-extract evidence with the configured AI backend "
        "(anti-fabrication gated)."
    )

    service = get_case_service()
    cases_with_evidence = [
        c.case_id for c in service.list_cases()
        if service.evidence.count_for_case(c.case_id) > 0
    ]
    if not cases_with_evidence:
        st.info("No assembled evidence yet. Assemble a case in Document Assembly.")
        return

    selected = st.selectbox("Case", cases_with_evidence, key="quality_case_select")

    if st.button("Score evidence quality", type="primary", key="quality_run"):
        assessments = service.score_evidence(selected)
        weak = sum(1 for a in assessments if a.is_weak)
        st.success(f"Scored {len(assessments)} evidence reference(s); {weak} weak.")

    assessments = service.list_evidence_quality(selected)
    if not assessments:
        st.info("Click **Score evidence quality** to assess this case's evidence.")
        return

    ev_by_id = {e.evidence_id: e for e in service.list_evidence(selected)}
    rows = []
    for a in assessments:
        ev = ev_by_id.get(a.evidence_id)
        rows.append(
            {
                "fact": (ev.fact_type if ev else "?"),
                "value": (ev.normalized_fact.split(": ", 1)[-1] if ev else ""),
                "overall": f"{a.overall_score:.0%}",
                "complete": f"{a.completeness_score:.0%}",
                "relevance": f"{a.relevance_score:.0%}",
                "consistency": f"{a.consistency_score:.0%}",
                "traceability": f"{a.traceability_score:.0%}",
                "weak": "⚠️" if a.is_weak else "",
                "issues": "; ".join(a.issues) or "—",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    weak = [a for a in assessments if a.is_weak]
    if weak:
        st.warning(f"{len(weak)} weak evidence reference(s) — review in the Reviewer Workbench.")


# --------------------------------------------------------------------------- #
# Tab: Reviewer Workbench (Milestone 10)
# --------------------------------------------------------------------------- #
_EV_DECISION_RENDER = {
    "APPROVE": st.success,
    "REJECT": st.error,
    "FLAG": st.warning,
    "PENDING": st.info,
}


def render_reviewer_workbench_tab() -> None:
    st.caption(
        "Validate evidence one item at a time: read the source quote, see "
        "quality scores and supporting/conflicting evidence, then approve, "
        "reject, or flag. Rejected evidence is excluded from downstream use."
    )

    service = get_case_service()
    cases_with_evidence = [
        c.case_id for c in service.list_cases()
        if service.evidence.count_for_case(c.case_id) > 0
    ]
    if not cases_with_evidence:
        st.info("No assembled evidence yet. Assemble a case first.")
        return

    selected = st.selectbox("Case", cases_with_evidence, key="wb_case_select")
    reviewer = st.text_input("Reviewer name", key="wb_reviewer")

    views = service.build_evidence_views(selected)
    if not views:
        st.info("No evidence to review.")
        return

    approved = len(service.workbench.approved_evidence_ids(selected))
    rejected = len(service.workbench.rejected_evidence_ids(selected))
    c1, c2, c3 = st.columns(3)
    c1.metric("Evidence", len(views))
    c2.metric("Approved", approved)
    c3.metric("Rejected", rejected)

    for view in views:
        ev = view.evidence
        value = ev.normalized_fact.split(": ", 1)[-1]
        renderer = _EV_DECISION_RENDER.get(view.status, st.info)
        renderer(f"**{ev.fact_type}**: {value}  —  status: {view.status}")
        st.markdown(f"Source: {ev.citation()}  ·  confidence {ev.confidence_score:.0%}")
        st.code(ev.quoted_text or "(no quote)", language="text")

        if view.quality:
            q = view.quality
            st.caption(
                f"Quality {q.overall_score:.0%} "
                f"(complete {q.completeness_score:.0%}, relevance {q.relevance_score:.0%}, "
                f"consistency {q.consistency_score:.0%}, traceability {q.traceability_score:.0%})"
                + (f" ⚠️ {'; '.join(q.issues)}" if q.issues else "")
            )
        if view.conflicting:
            st.caption("Conflicting evidence:")
            for c in view.conflicting:
                st.caption(f"  - {c.normalized_fact.split(': ', 1)[-1]} {c.citation()}")
        if view.supporting:
            st.caption(f"Supporting evidence: {len(view.supporting)} other reference(s) agree.")

        col_a, col_r, col_f = st.columns(3)
        if col_a.button("Approve", key=f"appr_{ev.evidence_id}"):
            _record_ev_decision(service, selected, ev.evidence_id, reviewer, EvidenceDecision.APPROVE)
        if col_r.button("Reject", key=f"rej_{ev.evidence_id}"):
            _record_ev_decision(service, selected, ev.evidence_id, reviewer, EvidenceDecision.REJECT)
        if col_f.button("Flag", key=f"flag_{ev.evidence_id}"):
            _record_ev_decision(service, selected, ev.evidence_id, reviewer, EvidenceDecision.FLAG)
        st.divider()

    # Decision history.
    decisions = service.list_evidence_decisions(selected)
    if decisions:
        st.markdown("#### Evidence decision history")
        st.dataframe(
            [
                {
                    "evidence_id": d.evidence_id,
                    "decision": d.decision.value,
                    "reviewer": d.reviewer,
                    "comments": d.comments,
                    "timestamp": d.timestamp,
                }
                for d in decisions
            ],
            use_container_width=True,
            hide_index=True,
        )


def _record_ev_decision(service, case_id, evidence_id, reviewer, decision) -> None:
    if not reviewer.strip():
        st.error("Enter a reviewer name before recording a decision.")
        return
    service.record_evidence_decision(case_id, evidence_id, reviewer.strip(), decision)
    st.rerun()
