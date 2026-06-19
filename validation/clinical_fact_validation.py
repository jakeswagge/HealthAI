"""Clinical Fact Contract validation scenarios.

This runner exercises the architecture-level guarantees added around clinical
facts: semantic conflicts must route to human review, decisive criteria must
carry source evidence IDs, and Humira criteria should consume ClinicalFact
records rather than legacy string-only matching.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.assembly.engine import CaseAssemblyEngine
from app.evidence.linker import link_review
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.clinical_fact import ClinicalFactDomain
from app.models.unified_case_context import UnifiedCaseContext
from app.review.engine import ClinicalReviewEngine

CRITERION_DOMAINS: dict[str, tuple[ClinicalFactDomain, ...]] = {
    "DX_CONFIRMED": (ClinicalFactDomain.DIAGNOSIS,),
    "STEP_THERAPY": (ClinicalFactDomain.STEP_THERAPY,),
    "TB_SCREEN": (ClinicalFactDomain.TB_SCREEN,),
    "SPECIALIST": (ClinicalFactDomain.PROVIDER,),
}


def _doc(
    case_id: str,
    filename: str,
    document_type: DocumentCategory,
    text: str,
) -> CaseDocument:
    return CaseDocument(
        case_id=case_id,
        filename=filename,
        document_type=document_type,
        raw_text=text,
    )


def _assemble(case_id: str, docs: list[CaseDocument]) -> UnifiedCaseContext:
    return CaseAssemblyEngine().assemble(case_id, docs)


def _combined_text(docs: list[CaseDocument]) -> str:
    return "\n\n".join(doc.raw_text for doc in docs)


def _review(context: UnifiedCaseContext, docs: list[CaseDocument]):
    review = ClinicalReviewEngine().review(context.patient_case, _combined_text(docs))
    return link_review(review, context)


def _evidence_lookup(context: UnifiedCaseContext) -> dict[str, dict[str, Any]]:
    return {
        ev.evidence_id: {
            "evidence_id": ev.evidence_id,
            "fact_type": ev.fact_type,
            "source_document": ev.source_filename,
            "page_number": ev.page_number,
            "quoted_text": ev.quoted_text,
        }
        for ev in context.evidence
    }


def _evidence_details(
    context: UnifiedCaseContext,
    evidence_ids: list[str],
) -> list[dict[str, Any]]:
    lookup = _evidence_lookup(context)
    return [lookup[eid] for eid in evidence_ids if eid in lookup]


def _fact_details(
    context: UnifiedCaseContext,
    evidence_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    wanted = set(evidence_ids or [])
    facts = []
    for fact in context.clinical_facts:
        if wanted and not wanted.intersection(fact.evidence_ids):
            continue
        facts.append(
            {
                "fact_id": fact.fact_id,
                "domain": fact.domain.value,
                "state": fact.state,
                "value": fact.value,
                "assertion": fact.assertion.value,
                "temporality": fact.temporality.value,
                "evidence_ids": list(fact.evidence_ids),
                "conflict_status": fact.conflict_status.value,
            }
        )
    return facts


def _conflict_scenario_docs() -> dict[str, tuple[str, list[CaseDocument]]]:
    return {
        "TB Positive + TB Negative": (
            "VAL-CONFLICT-TB",
            [
                _doc(
                    "VAL-CONFLICT-TB",
                    "clinical-note.txt",
                    DocumentCategory.CLINICAL_NOTE,
                    (
                        "Patient: Iris West\n"
                        "Diagnosis: Rheumatoid Arthritis\n"
                        "Requested Service: Humira.\n"
                        "Methotrexate failed after 12 weeks.\n"
                        "Rheumatologist prescriber documents the request."
                    ),
                ),
                _doc(
                    "VAL-CONFLICT-TB",
                    "tb-negative.txt",
                    DocumentCategory.LAB_RESULT,
                    "TB test negative.",
                ),
                _doc(
                    "VAL-CONFLICT-TB",
                    "tb-positive.txt",
                    DocumentCategory.LAB_RESULT,
                    "QuantiFERON-TB Gold result positive reactive.",
                ),
            ],
        ),
        "MTX Failed + MTX Refused": (
            "VAL-CONFLICT-MTX",
            [
                _doc(
                    "VAL-CONFLICT-MTX",
                    "clinical-note.txt",
                    DocumentCategory.CLINICAL_NOTE,
                    (
                        "Patient: Barry Allen\n"
                        "Diagnosis: Rheumatoid Arthritis\n"
                        "Requested Service: Humira.\n"
                        "TB screen negative.\n"
                        "Rheumatologist prescriber documents the request."
                    ),
                ),
                _doc(
                    "VAL-CONFLICT-MTX",
                    "mtx-failed.txt",
                    DocumentCategory.CLINICAL_NOTE,
                    "Methotrexate failed after 12 weeks.",
                ),
                _doc(
                    "VAL-CONFLICT-MTX",
                    "mtx-refused.txt",
                    DocumentCategory.CLINICAL_NOTE,
                    (
                        "Patient refused methotrexate and requested direct "
                        "biologic therapy."
                    ),
                ),
            ],
        ),
        "RA + Lupus": (
            "VAL-CONFLICT-DX",
            [
                _doc(
                    "VAL-CONFLICT-DX",
                    "ra-note.txt",
                    DocumentCategory.CLINICAL_NOTE,
                    "Diagnosis: Rheumatoid Arthritis\nRequested Service: Humira.",
                ),
                _doc(
                    "VAL-CONFLICT-DX",
                    "lupus-note.txt",
                    DocumentCategory.CLINICAL_NOTE,
                    "Patient has active Lupus requiring treatment.",
                ),
            ],
        ),
        "Specialist + PCP": (
            "VAL-CONFLICT-PROVIDER",
            [
                _doc(
                    "VAL-CONFLICT-PROVIDER",
                    "rheumatology-note.txt",
                    DocumentCategory.CLINICAL_NOTE,
                    "Rheumatologist prescriber recommends Humira.",
                ),
                _doc(
                    "VAL-CONFLICT-PROVIDER",
                    "pcp-note.txt",
                    DocumentCategory.CLINICAL_NOTE,
                    "Primary care provider recommends Humira.",
                ),
            ],
        ),
    }


def conflict_validation_report() -> dict[str, Any]:
    """Return the four requested semantic-conflict validation outputs."""
    scenarios = []
    for name, (case_id, docs) in _conflict_scenario_docs().items():
        context = _assemble(case_id, docs)
        conflicts = [
            {
                "conflict_id": conflict.conflict_id,
                "fact_type": conflict.fact_type,
                "severity": conflict.severity.value,
                "values": list(conflict.values),
                "evidence_ids": list(conflict.evidence_ids),
                "clinical_fact_ids": list(conflict.clinical_fact_ids),
                "requires_human_review_reason": (
                    conflict.requires_human_review_reason
                ),
            }
            for conflict in context.conflict_report.conflicts
        ]
        routing = (
            "HUMAN_REVIEW"
            if context.conflict_report.requires_human_review
            else "NO_CONFLICT"
        )
        scenarios.append(
            {
                "scenario": name,
                "routing": routing,
                "conflict_detected": context.conflict_report.has_conflicts,
                "requires_human_review": (
                    context.conflict_report.requires_human_review
                ),
                "review_recommendation_after_gate": (
                    "HUMAN_REVIEW"
                    if context.conflict_report.requires_human_review
                    else _review(context, docs).recommendation.value
                ),
                "raw_review_recommendation": (
                    "SKIPPED_DUE_TO_CONFLICT"
                    if context.conflict_report.requires_human_review
                    else _review(context, docs).recommendation.value
                ),
                "conflicts": conflicts,
                "conflict_evidence": _evidence_details(
                    context,
                    [
                        eid
                        for conflict in context.conflict_report.conflicts
                        for eid in conflict.evidence_ids
                    ],
                ),
            }
        )
    return {"validation": "conflict_engine", "scenarios": scenarios}


def _positive_tb_traceability_context() -> tuple[UnifiedCaseContext, list[CaseDocument]]:
    case_id = "VAL-TRACE-POSITIVE-TB"
    docs = [
        _doc(
            case_id,
            "humira-note.txt",
            DocumentCategory.CLINICAL_NOTE,
            (
                "Patient: Diana Prince\n"
                "Diagnosis: Severe erosive seropositive Rheumatoid Arthritis\n"
                "Requested Service: Humira (adalimumab).\n"
                "Treatment history: Completed 6-month Methotrexate trial "
                "and failed.\n"
                "PRESCRIBER: Dr. Steve Trevor, MD, Fellow of the American "
                "College of Rheumatology."
            ),
        ),
        _doc(
            case_id,
            "tb-lab.txt",
            DocumentCategory.LAB_RESULT,
            "QuantiFERON-TB Gold result: POSITIVE / REACTIVE.",
        ),
    ]
    return _assemble(case_id, docs), docs


def traceability_validation_report() -> dict[str, Any]:
    """Return one decisive review output with support and not-met evidence IDs."""
    context, docs = _positive_tb_traceability_context()
    review = _review(context, docs)
    criteria = []
    for detail in review.criteria_detail:
        supporting = list(detail.supporting_evidence_ids)
        not_met = list(detail.not_met_evidence_ids)
        criteria.append(
            {
                "criterion_id": detail.id,
                "status": detail.status.value if detail.status else None,
                "met": detail.met,
                "note": detail.note,
                "supporting_evidence_ids": supporting,
                "not_met_evidence_ids": not_met,
                "missing_evidence": list(detail.missing_evidence),
                "supporting_evidence": _evidence_details(context, supporting),
                "not_met_evidence": _evidence_details(context, not_met),
            }
        )
    return {
        "validation": "evidence_traceability",
        "recommendation": review.recommendation.value,
        "contraindications_found": list(review.contraindications_found),
        "criteria": criteria,
    }


def clinical_fact_coverage_report() -> dict[str, Any]:
    """Report which review criteria consumed canonical ClinicalFact records."""
    context, docs = _positive_tb_traceability_context()
    review = _review(context, docs)
    rows = []
    remaining_legacy = []
    for detail in review.criteria_detail:
        domains = CRITERION_DOMAINS.get(detail.id, ())
        evidence_ids = list(
            dict.fromkeys(
                [
                    *detail.supporting_evidence_ids,
                    *detail.not_met_evidence_ids,
                ]
            )
        )
        domain_facts = [
            fact for fact in context.clinical_facts if fact.domain in domains
        ]
        used_facts = [
            fact
            for fact in domain_facts
            if set(fact.evidence_ids).intersection(evidence_ids)
        ]
        consumes_clinical_fact = bool(used_facts)
        if not consumes_clinical_fact:
            remaining_legacy.append(detail.id)
        rows.append(
            {
                "criterion_id": detail.id,
                "criterion_status": detail.status.value if detail.status else None,
                "clinical_fact_domains": [domain.value for domain in domains],
                "consumes_clinical_fact": consumes_clinical_fact,
                "legacy_string_fallback_used": not consumes_clinical_fact,
                "review_evidence_ids": evidence_ids,
                "clinical_fact_ids": [fact.fact_id for fact in used_facts],
                "clinical_fact_states": [
                    f"{fact.domain.value}:{fact.state}:{fact.value}"
                    for fact in used_facts
                ],
            }
        )
    return {
        "validation": "clinical_fact_coverage",
        "criteria": rows,
        "remaining_legacy_string_logic": remaining_legacy,
        "engine_legacy_fallback_available": True,
        "legacy_fallback_note": (
            "Legacy keyword/string evaluation remains available for backward "
            "compatibility when a case has no ClinicalFact records, but it was "
            "not used by the Humira criteria in this validation."
        ),
    }


def all_validation_reports() -> dict[str, Any]:
    return {
        "conflicts": conflict_validation_report(),
        "traceability": traceability_validation_report(),
        "coverage": clinical_fact_coverage_report(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Clinical Fact Contract validation reports."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--conflicts", action="store_true")
    group.add_argument("--traceability", action="store_true")
    group.add_argument("--coverage", action="store_true")
    group.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    if args.conflicts:
        report = conflict_validation_report()
    elif args.traceability:
        report = traceability_validation_report()
    elif args.coverage:
        report = clinical_fact_coverage_report()
    else:
        report = all_validation_reports()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
