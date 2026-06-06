"""Case export-package generation.

Builds a bundle for a :class:`CaseRecord`:

- ``case_summary.md``         human-readable overview
- ``patient_case.json``       the structured PatientCase (or null)
- ``review_result.json``      the ReviewResult (or null)
- ``appeal_letter.md``        the generated appeal letter (or a placeholder)
- ``audit_log.json``          the full audit trail for the case

When multi-document evidence is available (Milestone 6/7), the bundle also
includes:

- ``evidence_inventory.json`` every source-backed EvidenceReference
- ``conflict_report.json``    cross-document conflicts with severity
- ``traceability_report.md``  human-readable field/section -> source mapping

The bundle is returned as in-memory bytes (a ZIP) so it can be offered as a
Streamlit download or written to disk in tests. No cloud, no temp-file leakage.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from app.models.audit_event import AuditEvent
from app.models.case_record import CaseRecord
from app.models.conflict_report import ConflictReport
from app.models.conflict_resolution import AuthoritativeFact, ConflictResolution
from app.models.evidence_quality import EvidenceQualityAssessment
from app.models.evidence_reference import EvidenceReference
from app.models.evidence_review_decision import EvidenceReviewDecision
from app.models.explanation import (
    AppealExplanation,
    ReviewExplanation,
    TraceabilityChain,
)
from app.models.governance import (
    ApprovedEvidenceSet,
    GovernanceComplianceReport,
)
from app.models.operational_health import OperationalHealthReport
from app.models.ocr_result import OCRPageResult
from app.models.payer import PayerProfile
from app.models.reviewer_feedback import ReviewerFeedback


def _build_case_summary(record: CaseRecord) -> str:
    """Render a human-readable case summary as Markdown."""
    pc = record.patient_case
    rr = record.review_result
    al = record.appeal_letter
    latest = record.latest_decision()

    lines: list[str] = []
    lines.append(f"# Case Summary — {record.case_id}")
    lines.append("")
    lines.append(f"- Status: {record.status.value}")
    lines.append(f"- Created: {record.created_at}")
    lines.append(f"- Updated: {record.updated_at}")
    lines.append(f"- Source document: {record.source_filename or 'unknown'}")
    lines.append(f"- Assigned reviewer: {record.assigned_reviewer or 'unassigned'}")
    lines.append("")

    lines.append("## Patient")
    if pc:
        lines.append(f"- Name: {pc.patient_name or 'Documentation was not available'}")
        lines.append(f"- Member ID: {pc.member_id or 'Documentation was not available'}")
        lines.append(f"- Diagnosis: {pc.diagnosis or 'Documentation was not available'}")
        lines.append(f"- Requested service: {pc.requested_service or 'Documentation was not available'}")
        lines.append(f"- Original decision: {pc.decision.value}")
    else:
        lines.append("- No extracted patient case available.")
    lines.append("")

    lines.append("## Review")
    if rr:
        lines.append(f"- Recommendation: {rr.recommendation.value}")
        lines.append(f"- Guideline: {rr.guideline_id or 'none matched'}")
        lines.append(f"- Confidence: {rr.confidence_score:.0%}")
        if rr.matched_criteria:
            lines.append("- Matched criteria:")
            for c in rr.matched_criteria:
                lines.append(f"  - {c}")
        if rr.missing_criteria:
            lines.append("- Missing criteria:")
            for c in rr.missing_criteria:
                lines.append(f"  - {c}")
    else:
        lines.append("- No review result available.")
    lines.append("")

    lines.append("## Appeal")
    if al:
        lines.append(f"- Appeal ID: {al.appeal_id}")
        lines.append(f"- Confidence: {al.confidence_score:.0%}")
        lines.append(f"- {al.summary()}")
    else:
        lines.append("- No appeal letter generated.")
    lines.append("")

    lines.append("## Human Review")
    if latest:
        lines.append(f"- Decision: {latest.decision.value}")
        lines.append(f"- Reviewer: {latest.reviewer_name}")
        lines.append(f"- Comments: {latest.comments or '(none)'}")
        lines.append(f"- Timestamp: {latest.timestamp}")
    else:
        lines.append("- No human-review decision recorded.")
    lines.append("")

    lines.append(
        f"_Generated {datetime.now(timezone.utc).isoformat()} by HealthAI. "
        "Review and verify before any external use._"
    )
    return "\n".join(lines)


def _build_traceability_report(
    record: CaseRecord,
    evidence: list[EvidenceReference],
    conflict_report: ConflictReport | None,
) -> str:
    """Render a human-readable traceability report as Markdown."""
    lines: list[str] = []
    lines.append(f"# Traceability Report — {record.case_id}")
    lines.append("")
    lines.append(
        "This report links the case's structured facts, review, and appeal "
        "back to the source documents and pages they came from."
    )
    lines.append("")

    lines.append("## Evidence Inventory")
    if evidence:
        for ev in evidence:
            cite = ev.citation()
            lines.append(
                f"- **{ev.normalized_fact or ev.fact_type or 'fact'}** "
                f"{cite} - \"{ev.quoted_text}\" "
                f"[{ev.evidence_id}]"
            )
    else:
        lines.append("- No evidence references were recorded for this case.")
    lines.append("")

    # Appeal section -> evidence.
    al = record.appeal_letter
    lines.append("## Appeal Section Traceability")
    if al and al.section_evidence:
        ev_by_id = {e.evidence_id: e for e in evidence}
        for section, ids in al.section_evidence.items():
            lines.append(f"### {section}")
            if ids:
                for ev_id in ids:
                    ev = ev_by_id.get(ev_id)
                    if ev:
                        lines.append(f"- {ev.citation()} {ev.normalized_fact} [{ev_id}]")
                    else:
                        lines.append(f"- [{ev_id}]")
            else:
                lines.append("- No supporting evidence recorded.")
            lines.append("")
    else:
        lines.append("- No appeal section traceability recorded.")
        lines.append("")

    # Conflicts.
    lines.append("## Conflicts")
    if conflict_report and conflict_report.has_conflicts:
        for c in conflict_report.conflicts:
            lines.append(
                f"- **{c.fact_type}** ({c.severity.value}): {c.description}"
            )
    else:
        lines.append("- No conflicts detected.")
    lines.append("")

    lines.append(
        f"_Generated {datetime.now(timezone.utc).isoformat()} by HealthAI. "
        "Every cited fact is traceable to a source document and page._"
    )
    return "\n".join(lines)


def _build_ocr_traceability_report(
    record: CaseRecord,
    ocr_results: list[OCRPageResult],
    confidence_threshold: float = 0.60,
) -> str:
    """Render an OCR traceability report as Markdown."""
    lines: list[str] = []
    lines.append(f"# OCR Traceability Report — {record.case_id}")
    lines.append("")
    lines.append(
        "Per-page OCR provenance. Each page lists the processing method and "
        "OCR confidence; low-confidence pages are flagged for reviewer "
        "inspection. No text was fabricated; empty pages produced no evidence."
    )
    lines.append("")

    if not ocr_results:
        lines.append("- No OCR was performed for this case (text-layer documents only).")
        return "\n".join(lines)

    # Group by document.
    by_doc: dict[str, list[OCRPageResult]] = {}
    for r in ocr_results:
        by_doc.setdefault(r.document_id, []).append(r)

    for document_id, pages in by_doc.items():
        lines.append(f"## Document {document_id}")
        for p in sorted(pages, key=lambda x: x.page_number):
            flag = " ⚠️ LOW CONFIDENCE" if p.confidence < confidence_threshold else ""
            lines.append(
                f"- Page {p.page_number}: method={p.processing_method.value}, "
                f"confidence={p.confidence:.0%}{flag} [{p.ocr_id}]"
            )
            snippet = " ".join(p.raw_text.split())[:160]
            lines.append(f"  - text: \"{snippet}\"")
        lines.append("")

    lines.append(
        f"_Confidence threshold: {confidence_threshold:.0%}. Generated "
        f"{datetime.now(timezone.utc).isoformat()} by HealthAI._"
    )
    return "\n".join(lines)


def _document_classification(record: CaseRecord, ocr_results: list[OCRPageResult]) -> list[dict]:
    """Summarize per-document classification + OCR provenance for export."""
    by_doc: dict[str, list[OCRPageResult]] = {}
    for r in ocr_results:
        by_doc.setdefault(r.document_id, []).append(r)
    out: list[dict] = []
    for document_id, pages in by_doc.items():
        confs = [p.confidence for p in pages]
        methods = sorted({p.processing_method.value for p in pages})
        out.append(
            {
                "document_id": document_id,
                "page_count": len(pages),
                "processing_methods": methods,
                "mean_confidence": round(sum(confs) / len(confs), 4) if confs else 0.0,
            }
        )
    return out


def build_export_files(
    record: CaseRecord,
    audit_events: list[AuditEvent],
    evidence: list[EvidenceReference] | None = None,
    conflict_report: ConflictReport | None = None,
    authoritative_facts: list[AuthoritativeFact] | None = None,
    conflict_resolutions: list[ConflictResolution] | None = None,
    reviewer_feedback: list[ReviewerFeedback] | None = None,
    ocr_results: list[OCRPageResult] | None = None,
    evidence_quality: list[EvidenceQualityAssessment] | None = None,
    evidence_review_decisions: list[EvidenceReviewDecision] | None = None,
    governance_report: GovernanceComplianceReport | None = None,
    quality_analytics: dict | None = None,
    approved_evidence_set: ApprovedEvidenceSet | None = None,
    all_evidence: list[EvidenceReference] | None = None,
    review_explanation: ReviewExplanation | None = None,
    appeal_explanation: AppealExplanation | None = None,
    traceability_chain: TraceabilityChain | None = None,
    payer_profile: PayerProfile | None = None,
    operational_health: OperationalHealthReport | None = None,
    validation_report: dict | None = None,
) -> dict[str, str]:
    """Return a mapping of filename -> text content for the export bundle.

    The core five files are always produced. When ``evidence`` is provided
    (Milestone 6/7), three traceability files are added. When Milestone 8 data
    is provided (authoritative facts / resolutions / feedback), three more files
    are added. All additions are backward-compatible.
    """
    patient_case_json = (
        record.patient_case.model_dump_json(indent=2)
        if record.patient_case
        else "null"
    )
    review_result_json = (
        record.review_result.model_dump_json(indent=2)
        if record.review_result
        else "null"
    )
    appeal_letter_md = (
        record.appeal_letter.letter_text
        if record.appeal_letter and record.appeal_letter.has_letter
        else "# Appeal Letter\n\nNo appeal letter was generated for this case."
    )
    audit_log_json = json.dumps(
        [e.model_dump(mode="json") for e in audit_events], indent=2
    )

    files = {
        "case_summary.md": _build_case_summary(record),
        "patient_case.json": patient_case_json,
        "review_result.json": review_result_json,
        "appeal_letter.md": appeal_letter_md,
        "audit_log.json": audit_log_json,
    }

    if evidence is not None:
        files["evidence_inventory.json"] = json.dumps(
            [e.model_dump(mode="json") for e in evidence], indent=2
        )
        files["conflict_report.json"] = (
            conflict_report.model_dump_json(indent=2)
            if conflict_report is not None
            else json.dumps({"case_id": record.case_id, "conflicts": []}, indent=2)
        )
        files["traceability_report.md"] = _build_traceability_report(
            record, evidence, conflict_report
        )

    # Milestone 8: authoritative facts, resolutions, reviewer feedback.
    if authoritative_facts is not None:
        files["authoritative_facts.json"] = json.dumps(
            [f.model_dump(mode="json") for f in authoritative_facts], indent=2
        )
    if conflict_resolutions is not None:
        files["conflict_resolutions.json"] = json.dumps(
            [r.model_dump(mode="json") for r in conflict_resolutions], indent=2
        )
    if reviewer_feedback is not None:
        files["reviewer_feedback.json"] = json.dumps(
            [f.model_dump(mode="json") for f in reviewer_feedback], indent=2
        )

    # Milestone 9: OCR results, classification, OCR traceability.
    if ocr_results is not None:
        files["ocr_results.json"] = json.dumps(
            [o.model_dump(mode="json") for o in ocr_results], indent=2
        )
        # Document classification derived from the patient case + record.
        classification = {
            "case_id": record.case_id,
            "documents": _document_classification(record, ocr_results),
        }
        files["document_classification.json"] = json.dumps(classification, indent=2)
        files["ocr_traceability_report.md"] = _build_ocr_traceability_report(
            record, ocr_results
        )

    # Milestone 10: evidence quality + reviewer evidence decisions.
    if evidence_quality is not None:
        files["evidence_quality.json"] = json.dumps(
            [a.model_dump(mode="json") for a in evidence_quality], indent=2
        )
    if evidence_review_decisions is not None:
        files["evidence_review_decisions.json"] = json.dumps(
            [d.model_dump(mode="json") for d in evidence_review_decisions], indent=2
        )

    # Milestone 11: governance report, analytics, approved/excluded evidence.
    if governance_report is not None:
        files["governance_report.json"] = governance_report.model_dump_json(indent=2)
    if quality_analytics is not None:
        files["quality_analytics.json"] = json.dumps(quality_analytics, indent=2)
    if approved_evidence_set is not None:
        ev_by_id = {e.evidence_id: e for e in (all_evidence or [])}
        included = [
            ev_by_id[i].model_dump(mode="json")
            for i in approved_evidence_set.included_ids
            if i in ev_by_id
        ] if all_evidence else [{"evidence_id": i} for i in approved_evidence_set.included_ids]
        files["approved_evidence.json"] = json.dumps(
            {
                "case_id": approved_evidence_set.case_id,
                "mode": approved_evidence_set.mode.value,
                "included_count": approved_evidence_set.included_count,
                "evidence": included,
            },
            indent=2,
        )
        files["excluded_evidence.json"] = json.dumps(
            {
                "case_id": approved_evidence_set.case_id,
                "mode": approved_evidence_set.mode.value,
                "excluded_count": approved_evidence_set.excluded_count,
                "excluded": [e.model_dump(mode="json") for e in approved_evidence_set.excluded],
            },
            indent=2,
        )

    # Milestone 13: explainability + traceability chain.
    if review_explanation is not None:
        files["review_explanation.json"] = review_explanation.model_dump_json(indent=2)
    if appeal_explanation is not None:
        files["appeal_explanation.json"] = appeal_explanation.model_dump_json(indent=2)
    if traceability_chain is not None:
        files["traceability_chain.json"] = traceability_chain.model_dump_json(indent=2)

    # Final Milestone: payer profile, operational health, validation report.
    if payer_profile is not None:
        files["payer_profile.json"] = payer_profile.model_dump_json(indent=2)
    if operational_health is not None:
        files["operational_health.json"] = json.dumps(
            operational_health.as_dict(), indent=2
        )
    if validation_report is not None:
        files["validation_report.json"] = json.dumps(validation_report, indent=2)

    return files


def build_export_zip(
    record: CaseRecord,
    audit_events: list[AuditEvent],
    evidence: list[EvidenceReference] | None = None,
    conflict_report: ConflictReport | None = None,
    authoritative_facts: list[AuthoritativeFact] | None = None,
    conflict_resolutions: list[ConflictResolution] | None = None,
    reviewer_feedback: list[ReviewerFeedback] | None = None,
    ocr_results: list[OCRPageResult] | None = None,
    evidence_quality: list[EvidenceQualityAssessment] | None = None,
    evidence_review_decisions: list[EvidenceReviewDecision] | None = None,
    governance_report: GovernanceComplianceReport | None = None,
    quality_analytics: dict | None = None,
    approved_evidence_set: ApprovedEvidenceSet | None = None,
    all_evidence: list[EvidenceReference] | None = None,
    review_explanation: ReviewExplanation | None = None,
    appeal_explanation: AppealExplanation | None = None,
    traceability_chain: TraceabilityChain | None = None,
    payer_profile: PayerProfile | None = None,
    operational_health: OperationalHealthReport | None = None,
    validation_report: dict | None = None,
) -> bytes:
    """Build the export bundle as ZIP bytes."""
    files = build_export_files(
        record,
        audit_events,
        evidence,
        conflict_report,
        authoritative_facts,
        conflict_resolutions,
        reviewer_feedback,
        ocr_results,
        evidence_quality,
        evidence_review_decisions,
        governance_report,
        quality_analytics,
        approved_evidence_set,
        all_evidence,
        review_explanation,
        appeal_explanation,
        traceability_chain,
        payer_profile,
        operational_health,
        validation_report,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()
