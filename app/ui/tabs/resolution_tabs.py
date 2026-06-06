"""Resolution tabs: Conflict Resolution (M8) and Reviewer Feedback (M8).

Moved verbatim from the old ``case_ui.py`` during the Milestone 12 UI split.
"""

from __future__ import annotations

import streamlit as st

from app.feedback.dataset import FeedbackDataset
from app.models.reviewer_feedback import FeedbackTarget, FeedbackVerdict
from app.ui.tabs.common import get_case_service


# --------------------------------------------------------------------------- #
# Tab: Conflict Resolution (Milestone 8)
# --------------------------------------------------------------------------- #
def _strip_citation(value: str) -> str:
    """Remove a trailing '(file, p.N)' citation from a displayed value."""
    idx = value.rfind(" (")
    return value[:idx].strip() if idx != -1 else value.strip()


def render_conflict_resolution_tab() -> None:
    st.caption(
        "Human reviewers resolve conflicting evidence and establish the "
        "authoritative case record. No conflict is resolved automatically; "
        "rejected values are always preserved and every decision is audited."
    )

    service = get_case_service()
    cases_with_docs = [
        c.case_id for c in service.list_cases()
        if service.documents.count_for_case(c.case_id) > 0
    ]
    if not cases_with_docs:
        st.info("No multi-document cases yet. Assemble a case in Document Assembly.")
        return

    selected = st.selectbox("Case", cases_with_docs, key="resolution_case_select")
    docs = service.list_documents(selected)
    report = service.assembly.assemble(selected, docs).conflict_report

    # Show current authoritative facts.
    with st.expander("Current authoritative facts"):
        facts = service.list_authoritative_facts(selected)
        if facts:
            st.dataframe(
                [
                    {
                        "fact": f.fact_type,
                        "value": f.value,
                        "source": f.resolution_source.value,
                        "document": f.source_document or "—",
                        "confidence": f"{f.confidence:.0%}",
                    }
                    for f in facts
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No authoritative facts yet (assemble the case first).")

    if not report.has_conflicts:
        st.success("No unresolved conflicts detected for this case.")
    else:
        st.markdown(f"#### {len(report.conflicts)} conflict(s) to resolve")
        for conflict in report.conflicts:
            with st.form(key=f"resolve_{conflict.conflict_id}"):
                st.markdown(
                    f"**{conflict.fact_type}** — severity {conflict.severity.value}"
                )
                st.caption(conflict.description)
                # Options are the cited conflicting values; store the clean value.
                display_values = conflict.values
                choice = st.radio(
                    "Authoritative value",
                    options=display_values,
                    key=f"choice_{conflict.conflict_id}",
                )
                reviewer = st.text_input(
                    "Reviewer name", key=f"rev_{conflict.conflict_id}"
                )
                justification = st.text_area(
                    "Justification", key=f"just_{conflict.conflict_id}"
                )
                submitted = st.form_submit_button("Submit resolution", type="primary")

            if submitted:
                if not reviewer.strip():
                    st.error("Reviewer name is required.")
                    continue
                chosen = _strip_citation(choice)
                rejected = [
                    _strip_citation(v) for v in display_values if v != choice
                ]
                service.resolve_conflict(
                    selected,
                    conflict.conflict_id,
                    conflict.fact_type,
                    chosen_value=chosen,
                    rejected_values=rejected,
                    reviewer_name=reviewer.strip(),
                    justification=justification,
                )
                st.success(
                    f"Resolved '{conflict.fact_type}' -> '{chosen}'. "
                    "Authoritative record updated; downstream review/appeal will "
                    "use this value. (rejected values preserved)."
                )
                st.rerun()

    # Resolution history.
    resolutions = service.list_resolutions(selected)
    if resolutions:
        st.markdown("#### Resolution history")
        st.dataframe(
            [
                {
                    "fact": r.fact_type,
                    "chosen": r.chosen_value,
                    "rejected": ", ".join(r.rejected_values),
                    "reviewer": r.reviewer_name,
                    "justification": r.justification,
                    "timestamp": r.timestamp,
                }
                for r in resolutions
            ],
            use_container_width=True,
            hide_index=True,
        )


# --------------------------------------------------------------------------- #
# Tab: Reviewer Feedback (Milestone 8)
# --------------------------------------------------------------------------- #
def render_reviewer_feedback_tab() -> None:
    st.caption(
        "Capture structured reviewer feedback on each pipeline stage. This is "
        "collected as learning data only — no model is retrained."
    )

    service = get_case_service()
    cases = service.list_cases()
    if not cases:
        st.info("No cases yet.")
        return

    selected = st.selectbox(
        "Case", [c.case_id for c in cases], key="feedback_case_select"
    )

    with st.form(key="feedback_form"):
        reviewer = st.text_input("Reviewer name")
        target = st.selectbox(
            "Target", [t.value for t in FeedbackTarget], key="feedback_target"
        )
        verdict = st.radio(
            "Verdict",
            options=[v.value for v in FeedbackVerdict],
            horizontal=True,
            key="feedback_verdict",
        )
        comments = st.text_area("Comments")
        submitted = st.form_submit_button("Submit feedback", type="primary")

    if submitted:
        if not reviewer.strip():
            st.error("Reviewer name is required.")
        else:
            service.record_reviewer_feedback(
                selected, reviewer.strip(), target, verdict, comments=comments
            )
            st.success(f"Recorded {verdict} feedback on {target} for {selected}.")
            st.rerun()

    # Feedback history.
    feedback = service.list_feedback(selected)
    st.markdown("#### Feedback history")
    if feedback:
        st.dataframe(
            [
                {
                    "target": f.target_type.value,
                    "verdict": f.feedback.value,
                    "reviewer": f.reviewer,
                    "comments": f.comments,
                    "timestamp": f.timestamp,
                }
                for f in feedback
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No feedback recorded for this case yet.")

    # Learning dataset export.
    st.markdown("#### Learning dataset")
    st.caption(
        "Structured corrections + resolutions + feedback, exportable for offline "
        "analysis. No machine learning is performed."
    )
    dataset = FeedbackDataset(
        feedback_repo=service.feedback,
        resolution_repo=service.resolutions,
        facts_repo=service.authoritative_facts,
    )
    st.download_button(
        "Download learning dataset (JSON)",
        data=dataset.export_json(selected),
        file_name=f"{selected}_learning_dataset.json",
        mime="application/json",
        key="feedback_dataset_download",
    )
