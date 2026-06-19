"""Automated clinical validation harness for HealthAI.

Runs a mixed scenario matrix through the production workflow boundary
(`CaseService`) and emits:

- validation/MASTER_VALIDATION_REPORT.md
- validation/MASTER_VALIDATION_RESULTS.json

The harness is intentionally deterministic by default: local review always runs,
and Gemini review runs only when the Gemini backend can be initialized or when
the caller forces it with CLI flags. Individual case failures are captured in
the report rather than stopping the suite.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.appeals.verifier import AppealVerifier
from app.assembly.engine import CaseAssemblyEngine
from app.cases.appeal_service import ExportBlockedError
from app.cases.service import CaseService
from app.evidence.linker import link_review
from app.governance.safety import SafetyGate
from app.guidelines.repository import get_default_repository
from app.models.case_document import DocumentCategory
from app.models.clinical_fact import ClinicalFactDomain
from app.models.governance import GovernanceSettings
from app.models.patient_case import Decision
from app.models.review_result import Recommendation, ReviewResult
from app.review.comparison import compare_reviews
from app.review.engine import ClinicalReviewEngine
from app.review.review_agent import GuidelineReviewAgent
from app.services.factory import get_llm_client
from app.services.llm_client import LLMError
from app.storage.database import connect, initialize_schema

DEFAULT_MATRIX = PROJECT_ROOT / "validation" / "test_matrix_cases.json"
DEFAULT_JSON_REPORT = PROJECT_ROOT / "validation" / "MASTER_VALIDATION_RESULTS.json"
DEFAULT_MD_REPORT = PROJECT_ROOT / "validation" / "MASTER_VALIDATION_REPORT.md"

STATE_DOMAINS = {
    "diagnosis_state": ClinicalFactDomain.DIAGNOSIS,
    "tb_state": ClinicalFactDomain.TB_SCREEN,
    "step_therapy_state": ClinicalFactDomain.STEP_THERAPY,
    "provider_state": ClinicalFactDomain.PROVIDER,
}

CONFLICT_TRIGGER_PATTERNS = (
    "tb positive + tb negative",
    "positive tb + negative tb",
    "ra + lupus",
    "failed mtx + refused mtx",
    "mtx failed + mtx refused",
    "specialist + pcp",
)


@dataclass
class BackendRun:
    name: str
    review: ReviewResult | None = None
    used_ai: bool = False
    available: bool = True
    error: str = ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pct(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _read_matrix(path: Path) -> list[dict]:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return data.get("cases", data.get("scenarios", []))
    return _starter_matrix()


def _starter_matrix() -> list[dict]:
    """Synthetic starter cases used when the external matrix is absent."""
    return [
        {
            "case_id": "TB-001",
            "scenario": "Positive TB Humira Request",
            "case_type": "Humira",
            "documents": [
                {
                    "filename": "clinical_note.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": (
                        "Patient: Diana Prince\n"
                        "Member ID: VAL-TB-001\n"
                        "Diagnosis: Severe erosive seropositive Rheumatoid Arthritis\n"
                        "Requested Service: Humira (adalimumab).\n"
                        "Treatment history: Completed 6-month Methotrexate trial "
                        "and failed.\n"
                        "PRESCRIBER: Dr. Steve Trevor, MD, Fellow of the "
                        "American College of Rheumatology.\n"
                        "Status: DENIED\n"
                        "Reason for Denial: Positive TB screening is a contraindication."
                    ),
                },
                {
                    "filename": "tb_lab.txt",
                    "document_type": "LAB_RESULT",
                    "text": "QuantiFERON-TB Gold result: POSITIVE / REACTIVE.",
                },
            ],
            "expected": {
                "recommendation": "DENY",
                "diagnosis_state": "ACTIVE",
                "tb_state": "POSITIVE",
                "step_therapy_state": "FAILED",
                "provider_state": "SPECIALIST",
                "human_review": False,
            },
        },
        {
            "case_id": "TB-002",
            "scenario": "Negative TB Completed MTX Humira Request",
            "case_type": "Humira",
            "documents": [
                {
                    "filename": "complete_note.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": (
                        "Patient: Clark Kent\n"
                        "Member ID: VAL-NGT-002\n"
                        "Diagnosis: Moderate to severe Rheumatoid Arthritis\n"
                        "Requested Service: Humira (adalimumab).\n"
                        "Methotrexate failed after 16 weeks.\n"
                        "TB screen negative.\n"
                        "Rheumatologist prescribing.\n"
                        "Status: DENIED\n"
                        "Reason for Denial: Prior authorization review requested."
                    ),
                },
            ],
            "expected": {
                "recommendation": "APPROVE",
                "diagnosis_state": "ACTIVE",
                "tb_state": "NEGATIVE",
                "step_therapy_state": "FAILED",
                "provider_state": "SPECIALIST",
                "human_review": False,
            },
            "appeal": True,
        },
        {
            "case_id": "STEP-REFUSED-001",
            "scenario": "Refused Methotrexate Humira Request",
            "case_type": "Humira",
            "documents": [
                {
                    "filename": "refusal_note.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": (
                        "Patient: Barry Allen\n"
                        "Member ID: VAL-STEP-001\n"
                        "Diagnosis: Moderate-to-severe Rheumatoid Arthritis\n"
                        "Requested Service: Humira (adalimumab).\n"
                        "Methotrexate 15mg weekly was recommended. Patient "
                        "refused to fill and refused to ingest the medication. "
                        "Patient is non-compliant.\n"
                        "Negative PPD TB test.\n"
                        "Prescriber is a Board Certified Rheumatologist.\n"
                        "Status: DENIED\n"
                        "Reason for Denial: Step therapy requirement not satisfied."
                    ),
                },
            ],
            "expected": {
                "recommendation": "DENY",
                "diagnosis_state": "ACTIVE",
                "tb_state": "NEGATIVE",
                "step_therapy_state": "REFUSED",
                "provider_state": "SPECIALIST",
                "human_review": False,
            },
        },
        {
            "case_id": "CONFLICT-TB-001",
            "scenario": "TB Positive + TB Negative",
            "case_type": "Conflict",
            "documents": [
                {
                    "filename": "note.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": (
                        "Patient: Iris West\n"
                        "Member ID: VAL-CONFLICT-TB\n"
                        "Diagnosis: Rheumatoid Arthritis\n"
                        "Requested Service: Humira.\n"
                        "Methotrexate failed after 12 weeks.\n"
                        "Rheumatologist prescriber documents the request."
                    ),
                },
                {
                    "filename": "tb_negative.txt",
                    "document_type": "LAB_RESULT",
                    "text": "TB test negative.",
                },
                {
                    "filename": "tb_positive.txt",
                    "document_type": "LAB_RESULT",
                    "text": "QuantiFERON-TB Gold result positive reactive.",
                },
            ],
            "expected": {
                "recommendation": "HUMAN_REVIEW",
                "tb_state": "CONFLICT",
                "step_therapy_state": "FAILED",
                "provider_state": "SPECIALIST",
                "human_review": True,
            },
        },
        {
            "case_id": "CONFLICT-MTX-001",
            "scenario": "MTX Failed + MTX Refused",
            "case_type": "Conflict",
            "documents": [
                {
                    "filename": "base_note.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": (
                        "Patient: Wally West\n"
                        "Member ID: VAL-CONFLICT-MTX\n"
                        "Diagnosis: Rheumatoid Arthritis\n"
                        "Requested Service: Humira.\n"
                        "TB screen negative.\n"
                        "Rheumatologist prescriber documents the request."
                    ),
                },
                {
                    "filename": "mtx_failed.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": "Methotrexate failed after 12 weeks.",
                },
                {
                    "filename": "mtx_refused.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": (
                        "Patient refused methotrexate and requested direct "
                        "biologic therapy."
                    ),
                },
            ],
            "expected": {
                "recommendation": "HUMAN_REVIEW",
                "tb_state": "NEGATIVE",
                "step_therapy_state": "CONFLICT",
                "provider_state": "SPECIALIST",
                "human_review": True,
            },
        },
        {
            "case_id": "CONFLICT-DX-001",
            "scenario": "RA + Lupus",
            "case_type": "Conflict",
            "documents": [
                {
                    "filename": "ra_note.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": (
                        "Member ID: VAL-CONFLICT-DX\n"
                        "Diagnosis: Rheumatoid Arthritis\n"
                        "Requested Service: Humira."
                    ),
                },
                {
                    "filename": "lupus_note.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": "Patient has active Lupus requiring treatment.",
                },
            ],
            "expected": {
                "recommendation": "HUMAN_REVIEW",
                "diagnosis_state": "CONFLICT",
                "human_review": True,
            },
        },
        {
            "case_id": "CONFLICT-PROVIDER-001",
            "scenario": "Specialist + PCP",
            "case_type": "Conflict",
            "documents": [
                {
                    "filename": "rheumatology_note.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": "Rheumatologist prescriber recommends Humira.",
                },
                {
                    "filename": "pcp_note.txt",
                    "document_type": "CLINICAL_NOTE",
                    "text": "Primary care provider recommends Humira.",
                },
            ],
            "expected": {
                "recommendation": "HUMAN_REVIEW",
                "provider_state": "CONFLICT",
                "human_review": True,
            },
        },
    ]


@contextmanager
def _temporary_env(updates: dict[str, str | None]):
    old = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _new_service() -> CaseService:
    conn = connect(":memory:")
    initialize_schema(conn)
    return CaseService(conn=conn)


def _document_type(value: str | None) -> DocumentCategory:
    if not value:
        return DocumentCategory.OTHER
    try:
        return DocumentCategory(str(value).strip().upper())
    except ValueError:
        return DocumentCategory.OTHER


def _ingest_documents(service: CaseService, case_id: str, documents: list[dict]) -> None:
    for index, doc in enumerate(documents, start=1):
        filename = doc.get("filename") or f"document_{index}.txt"
        text = str(doc.get("text") or doc.get("raw_text") or "")
        document_type = _document_type(doc.get("document_type") or doc.get("type"))
        service.add_document(
            case_id,
            filename,
            text,
            page_count=max(1, text.count("\f") + 1),
            document_type=document_type,
        )


def _combined_text(documents: list[dict]) -> str:
    return "\n\n".join(str(doc.get("text") or doc.get("raw_text") or "") for doc in documents)


def _domain_states(context, domain: ClinicalFactDomain) -> list[str]:
    return [
        fact.state
        for fact in context.clinical_facts
        if fact.domain == domain
    ]


def _dominant_state(context, domain: ClinicalFactDomain) -> str:
    states = _domain_states(context, domain)
    if not states:
        return "UNKNOWN"
    conflicted = [
        fact for fact in context.clinical_facts
        if fact.domain == domain and fact.conflict_status.value == "CONFLICTED"
    ]
    if conflicted or len(set(states)) > 1:
        return "CONFLICT"
    return states[0]


def _clinical_fact_summary(context) -> dict[str, Any]:
    states = {
        key: _dominant_state(context, domain)
        for key, domain in STATE_DOMAINS.items()
    }
    states["conflict_state"] = (
        "CONFLICT" if context.conflict_report.has_conflicts else "NONE"
    )
    return {
        **states,
        "facts": [
            {
                "fact_id": fact.fact_id,
                "domain": fact.domain.value,
                "state": fact.state,
                "value": fact.value,
                "assertion": fact.assertion.value,
                "temporality": fact.temporality.value,
                "confidence_score": fact.confidence_score,
                "evidence_ids": list(fact.evidence_ids),
                "conflict_status": fact.conflict_status.value,
            }
            for fact in context.clinical_facts
        ],
    }


def _conflict_summary(context) -> dict[str, Any]:
    return {
        "has_conflicts": context.conflict_report.has_conflicts,
        "requires_human_review": context.conflict_report.requires_human_review,
        "highest_severity": (
            context.conflict_report.highest_severity.value
            if context.conflict_report.highest_severity
            else None
        ),
        "conflicts": [
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
        ],
    }


def _review_summary(review: ReviewResult | None) -> dict[str, Any] | None:
    if review is None:
        return None
    return {
        "recommendation": review.recommendation.value,
        "confidence_score": review.confidence_score,
        "matched_criteria": list(review.matched_criteria),
        "missing_criteria": list(review.missing_criteria),
        "contraindications_found": list(review.contraindications_found),
        "rationale": review.rationale,
        "review_backend": review.review_backend,
        "review_model": review.review_model,
        "generated_by_ai": review.generated_by_ai,
        "safety_gate": _jsonable(review.safety_gate),
        "criteria_detail": [
            {
                "id": detail.id,
                "description": detail.description,
                "status": detail.status.value if detail.status else None,
                "met": detail.met,
                "supporting_evidence_ids": list(detail.supporting_evidence_ids),
                "not_met_evidence_ids": list(detail.not_met_evidence_ids),
                "missing_evidence": list(detail.missing_evidence),
                "note": detail.note,
                "confidence_score": detail.confidence_score,
            }
            for detail in review.criteria_detail
        ],
    }


def _traceability_findings(review: ReviewResult | None) -> list[dict[str, Any]]:
    if review is None:
        return [
            {
                "severity": "HIGH",
                "subsystem": "Evidence Traceability",
                "message": "Review did not run, so criterion traceability could not be validated.",
            }
        ]
    findings: list[dict[str, Any]] = []
    for detail in review.criteria_detail:
        has_support = bool(detail.supporting_evidence_ids)
        has_not_met = bool(detail.not_met_evidence_ids)
        has_missing = bool(detail.missing_evidence)
        if detail.met and not has_support:
            findings.append(
                {
                    "severity": "HIGH",
                    "subsystem": "Evidence Traceability",
                    "criterion_id": detail.id,
                    "message": "Met criterion lacks supporting_evidence_ids.",
                }
            )
        if not detail.met and not (has_not_met or has_missing):
            findings.append(
                {
                    "severity": "HIGH",
                    "subsystem": "Evidence Traceability",
                    "criterion_id": detail.id,
                    "message": "Not-met criterion lacks not_met_evidence_ids or missing_evidence.",
                }
            )
    return findings


def _review_accuracy(expected: dict, review: ReviewResult | None, workflow_decision: str) -> bool | None:
    expected_rec = str(expected.get("recommendation", "")).upper()
    if not expected_rec:
        return None
    if expected_rec == "HUMAN_REVIEW":
        return workflow_decision == "HUMAN_REVIEW"
    if review is None:
        return False
    return review.recommendation.value == expected_rec


def _expected_human_review(case: dict, expected: dict, conflict_report: dict) -> bool:
    if "human_review" in expected:
        return bool(expected["human_review"])
    scenario = str(case.get("scenario") or case.get("title") or "").lower()
    if any(pattern in scenario for pattern in CONFLICT_TRIGGER_PATTERNS):
        return True
    if conflict_report.get("highest_severity") in {"HIGH", "MEDIUM"}:
        return True
    return False


def _state_findings(expected: dict, actual: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for key in ("diagnosis_state", "tb_state", "step_therapy_state", "provider_state"):
        if key not in expected:
            continue
        exp = str(expected[key]).upper()
        got = str(actual.get(key, "UNKNOWN")).upper()
        if got != exp:
            findings.append(
                {
                    "severity": "HIGH",
                    "subsystem": "ClinicalFact",
                    "message": f"{key} drift: expected {exp}, got {got}.",
                    "expected": exp,
                    "actual": got,
                }
            )
    return findings


def _governance_findings(
    *,
    compliance: dict[str, Any] | None,
    review: ReviewResult | None,
    export_gate: dict[str, Any] | None,
    conflict_report: dict[str, Any],
    expected_human_review: bool,
    workflow_decision: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if compliance is None:
        findings.append(
            {
                "severity": "HIGH",
                "subsystem": "Governance",
                "message": "Governance compliance check did not run.",
            }
        )
    if review is None or not isinstance(review.safety_gate, dict) or not review.safety_gate:
        findings.append(
            {
                "severity": "HIGH",
                "subsystem": "Governance",
                "message": "Review safety gate did not run or was not attached.",
            }
        )
    if export_gate is None:
        findings.append(
            {
                "severity": "MEDIUM",
                "subsystem": "Governance",
                "message": "Export safety gate did not run.",
            }
        )
    if conflict_report.get("requires_human_review") and workflow_decision != "HUMAN_REVIEW":
        findings.append(
            {
                "severity": "CRITICAL",
                "subsystem": "Human Review Escalation",
                "message": "Conflict case produced a production decision instead of HUMAN_REVIEW.",
            }
        )
    if expected_human_review and workflow_decision != "HUMAN_REVIEW":
        findings.append(
            {
                "severity": "CRITICAL",
                "subsystem": "Human Review Escalation",
                "message": "Expected human-review routing was not honored.",
            }
        )
    return findings


def _likely_divergence_root_cause(local: ReviewResult | None, gemini: ReviewResult | None) -> str:
    if local is None or gemini is None:
        return "One backend did not produce a review result."
    local_ids = {
        ev_id
        for detail in local.criteria_detail
        for ev_id in [*detail.supporting_evidence_ids, *detail.not_met_evidence_ids]
    }
    ai_ids = {
        ev_id
        for detail in gemini.criteria_detail
        for ev_id in [*detail.supporting_evidence_ids, *detail.not_met_evidence_ids]
    }
    if local_ids and not ai_ids:
        return "AI review appears to have lost criterion-level evidence linkage."
    if local.contraindications_found and not gemini.contraindications_found:
        return "AI review did not preserve deterministic contraindication findings."
    return "Backend interpretation or guideline criterion status differs."


def _complexity_for_subsystem(subsystem: str) -> str:
    if subsystem in {"Evidence Traceability", "ClinicalFact"}:
        return "Surgical"
    if subsystem in {"Conflict Detection", "Human Review Escalation", "Governance"}:
        return "Moderate"
    return "Moderate"


def _recommended_fix(subsystem: str) -> str:
    return {
        "Extraction": "Add or tune deterministic extraction pattern and ClinicalFact state mapping.",
        "Assembly": "Inspect evidence-to-fact synthesis and conflict-state propagation.",
        "ClinicalFact": "Normalize expected state through the ClinicalFact contract before review.",
        "Conflict Detection": "Add typed semantic conflict rule with evidence and ClinicalFact IDs.",
        "Clinical Review": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
        "Evidence Traceability": "Attach supporting/not-met evidence IDs at the criterion boundary.",
        "Governance": "Route the workflow through governed service methods and safety gates.",
        "Human Review Escalation": "Fail closed on unresolved medium/high conflicts.",
        "Appeals": "Verify appeal claims against approved evidence before export.",
        "Explainability": "Generate traceability chain and explanation from approved evidence set.",
        "AI vs Local": "Compare AI output against deterministic guardrails and inspect criterion-level evidence.",
    }.get(subsystem, "Review subsystem output and add regression coverage.")


def _severity_rank(severity: str) -> int:
    return {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(severity, 4)


def _make_issue(
    *,
    subsystem: str,
    severity: str,
    message: str,
    root_cause: str,
    case_id: str,
    scenario: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "scenario": scenario,
        "subsystem": subsystem,
        "severity": severity,
        "message": message,
        "root_cause_hypothesis": root_cause,
        "recommended_fix": _recommended_fix(subsystem),
        "fix_complexity": _complexity_for_subsystem(subsystem),
        **(extra or {}),
    }


def _run_local_review(context, documents: list[dict], settings: GovernanceSettings) -> BackendRun:
    try:
        review = ClinicalReviewEngine().review(context.patient_case, _combined_text(documents))
        review = link_review(review, context)
        conflict_reasons = [
            conflict.requires_human_review_reason or conflict.description
            for conflict in context.conflict_report.conflicts
        ]
        if conflict_reasons:
            gate = dict(review.safety_gate or {})
            gate["unresolved_conflicts"] = conflict_reasons
            review.safety_gate = gate
        gate = SafetyGate(settings).review(review)
        gate_payload = gate.model_dump(mode="json")
        for key in ("unresolved_conflicts", "validation_errors", "comparison"):
            if key in (review.safety_gate or {}):
                gate_payload[key] = review.safety_gate[key]
        review.safety_gate = gate_payload
        return BackendRun(name="local", review=review, used_ai=False)
    except Exception as exc:  # noqa: BLE001
        return BackendRun(name="local", available=False, error=f"{type(exc).__name__}: {exc}")


def _run_gemini_review(
    context,
    documents: list[dict],
    settings: GovernanceSettings,
    mode: str,
) -> BackendRun:
    if mode == "skip":
        return BackendRun(name="gemini", available=False, error="Skipped by CLI mode.")
    try:
        llm = get_llm_client(force="gemini")
    except Exception as exc:  # noqa: BLE001
        if mode == "force":
            return BackendRun(name="gemini", available=False, error=f"{type(exc).__name__}: {exc}")
        return BackendRun(name="gemini", available=False, error=f"Gemini unavailable: {exc}")

    try:
        agent = GuidelineReviewAgent(llm_client=llm, repository=get_default_repository())
        result = agent.review(context.patient_case, _combined_text(documents))
        review = link_review(result.result, context)
        conflict_reasons = [
            conflict.requires_human_review_reason or conflict.description
            for conflict in context.conflict_report.conflicts
        ]
        if conflict_reasons:
            gate = dict(review.safety_gate or {})
            gate["unresolved_conflicts"] = conflict_reasons
            review.safety_gate = gate
        gate = SafetyGate(settings).review(review)
        gate_payload = gate.model_dump(mode="json")
        for key in ("unresolved_conflicts", "validation_errors", "comparison"):
            if key in (review.safety_gate or {}):
                gate_payload[key] = review.safety_gate[key]
        review.safety_gate = gate_payload
        return BackendRun(
            name="gemini",
            review=review,
            used_ai=result.used_ai,
            available=True,
            error="; ".join(result.errors),
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001
        return BackendRun(name="gemini", available=False, error=f"{type(exc).__name__}: {exc}")


def _run_appeal_path(service: CaseService, case_id: str, settings: GovernanceSettings, should_run: bool) -> dict[str, Any]:
    if not should_run:
        return {"ran": False, "outcome": "SKIPPED"}
    try:
        with _temporary_env({"HEALTHAI_LLM_BACKEND": "local"}):
            payer_appeal = service.appeal_with_payer(case_id, "DEFAULT", settings)
        appeal = payer_appeal.appeal
        record = service.attach_appeal(case_id, appeal)
        return {
            "ran": True,
            "outcome": "GENERATED",
            "used_ai": payer_appeal.governed.used_ai,
            "appeal_id": appeal.appeal_id,
            "verification": _jsonable(appeal.verification),
            "safety_gate": _jsonable(appeal.safety_gate),
            "evidence_ids": list(appeal.evidence_ids),
            "explanation": _jsonable(payer_appeal.governed.explanation),
            "record_status": record.status.value,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ran": True,
            "outcome": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _export_gate(service: CaseService, case_id: str, settings: GovernanceSettings) -> dict[str, Any]:
    try:
        gate = service.export_safety_gate(case_id, settings)
        mark_exported_error = ""
        try:
            service.mark_exported(case_id)
            mark_exported = "PASS"
        except ExportBlockedError as exc:
            mark_exported = "BLOCKED"
            mark_exported_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            mark_exported = "ERROR"
            mark_exported_error = f"{type(exc).__name__}: {exc}"
        return {
            "ran": True,
            "gate": gate.model_dump(mode="json"),
            "mark_exported": mark_exported,
            "mark_exported_error": mark_exported_error,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ran": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_case(case: dict[str, Any], *, gemini_mode: str, settings: GovernanceSettings) -> dict[str, Any]:
    case_id_label = str(case.get("case_id") or case.get("scenario_id") or "CASE")
    scenario = str(case.get("scenario") or case.get("title") or case_id_label)
    print(f"[validation] {case_id_label}: {scenario}")
    service = _new_service()
    issues: list[dict[str, Any]] = []
    try:
        record = service.create_case(case_id_label)
        runtime_case_id = record.case_id
        documents = list(case.get("documents") or [])
        _ingest_documents(service, runtime_case_id, documents)

        context = service.assemble_case(runtime_case_id)
        evidence_quality = service.score_evidence(runtime_case_id)
        with _temporary_env({"HEALTHAI_LLM_BACKEND": "local"}):
            governed_review = service.generate_governed_review(runtime_case_id, settings)
        governed_review_result = governed_review.review
        governed_review_result.safety_gate = SafetyGate(settings).review(
            governed_review_result
        ).model_dump(mode="json")

        clinical_facts = _clinical_fact_summary(context)
        conflicts = _conflict_summary(context)
        expected = dict(case.get("expected") or case.get("expectations", {}).get("DEFAULT", {}) or {})
        expected_human = _expected_human_review(case, expected, conflicts)

        local = _run_local_review(context, documents, settings)
        if local.review is not None:
            service.attach_review(runtime_case_id, local.review)

        gemini = _run_gemini_review(context, documents, settings, gemini_mode)
        comparison = None
        divergence = None
        if local.review is not None and gemini.review is not None:
            comparison = compare_reviews(
                local.review,
                gemini.review,
                known_evidence_ids={ev.evidence_id for ev in context.evidence},
                confidence_threshold=settings.confidence_threshold,
            ).as_dict()
            if local.review.recommendation != gemini.review.recommendation:
                divergence = {
                    "case_id": case_id_label,
                    "scenario": scenario,
                    "local_output": _review_summary(local.review),
                    "gemini_output": _review_summary(gemini.review),
                    "likely_root_cause": _likely_divergence_root_cause(
                        local.review,
                        gemini.review,
                    ),
                    "recommended_investigation": (
                        "Inspect ClinicalFact states, criterion evidence IDs, "
                        "and deterministic guardrail comparison for this case."
                    ),
                }

        workflow_decision = (
            "HUMAN_REVIEW"
            if conflicts["requires_human_review"]
            or (local.review and local.review.safety_gate.get("status") == "HUMAN_REVIEW_REQUIRED")
            else (local.review.recommendation.value if local.review else "ERROR")
        )

        should_appeal = bool(
            case.get("appeal")
            or case.get("appeal_expected")
            or (
                local.review is not None
                and local.review.recommendation in {Recommendation.APPROVE, Recommendation.DENY}
                and context.patient_case.decision is Decision.DENIED
                and not conflicts["requires_human_review"]
            )
        )
        appeal = _run_appeal_path(service, runtime_case_id, settings, should_appeal)

        compliance_report = None
        try:
            compliance = service.check_compliance(runtime_case_id, settings)
            compliance_report = compliance.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            issues.append(
                _make_issue(
                    subsystem="Governance",
                    severity="HIGH",
                    message=f"Compliance check failed: {type(exc).__name__}: {exc}",
                    root_cause="Governance service failed during compliance evaluation.",
                    case_id=case_id_label,
                    scenario=scenario,
                )
            )

        export = _export_gate(service, runtime_case_id, settings)
        traceability_chain = None
        try:
            traceability_chain = service.traceability_chain(runtime_case_id, settings).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            issues.append(
                _make_issue(
                    subsystem="Explainability",
                    severity="MEDIUM",
                    message=f"Traceability chain failed: {type(exc).__name__}: {exc}",
                    root_cause="Explainability service could not build full evidence lineage.",
                    case_id=case_id_label,
                    scenario=scenario,
                )
            )

        for finding in _state_findings(expected, clinical_facts):
            issues.append(
                _make_issue(
                    subsystem=finding["subsystem"],
                    severity=finding["severity"],
                    message=finding["message"],
                    root_cause="ClinicalFact generation or state normalization drift.",
                    case_id=case_id_label,
                    scenario=scenario,
                    extra={"expected": finding.get("expected"), "actual": finding.get("actual")},
                )
            )

        for finding in _traceability_findings(local.review):
            issues.append(
                _make_issue(
                    subsystem=finding["subsystem"],
                    severity=finding["severity"],
                    message=finding["message"],
                    root_cause="Review criterion was not linked to EvidenceReference IDs.",
                    case_id=case_id_label,
                    scenario=scenario,
                    extra={"criterion_id": finding.get("criterion_id")},
                )
            )

        for finding in _governance_findings(
            compliance=compliance_report,
            review=local.review,
            export_gate=export.get("gate") if export else None,
            conflict_report=conflicts,
            expected_human_review=expected_human,
            workflow_decision=workflow_decision,
        ):
            issues.append(
                _make_issue(
                    subsystem=finding["subsystem"],
                    severity=finding["severity"],
                    message=finding["message"],
                    root_cause="Governance or safety gate did not enforce fail-closed routing.",
                    case_id=case_id_label,
                    scenario=scenario,
                )
            )

        local_accuracy = _review_accuracy(expected, local.review, workflow_decision)
        gemini_accuracy = _review_accuracy(expected, gemini.review, workflow_decision) if gemini.review else None
        if local_accuracy is False:
            issues.append(
                _make_issue(
                    subsystem="Clinical Review",
                    severity="HIGH",
                    message=(
                        f"Local outcome drift: expected "
                        f"{expected.get('recommendation')}, got {workflow_decision}."
                    ),
                    root_cause="Local review or workflow routing disagreed with expected outcome.",
                    case_id=case_id_label,
                    scenario=scenario,
                )
            )
        if gemini.review is not None and gemini_accuracy is False:
            issues.append(
                _make_issue(
                    subsystem="AI vs Local",
                    severity="MEDIUM",
                    message=(
                        f"Gemini outcome drift: expected "
                        f"{expected.get('recommendation')}, got {gemini.review.recommendation.value}."
                    ),
                    root_cause="Gemini review disagreed with expected clinical outcome.",
                    case_id=case_id_label,
                    scenario=scenario,
                )
            )
        if divergence:
            issues.append(
                _make_issue(
                    subsystem="AI vs Local",
                    severity="MEDIUM",
                    message="Gemini recommendation differs from local recommendation.",
                    root_cause=divergence["likely_root_cause"],
                    case_id=case_id_label,
                    scenario=scenario,
                )
            )

        result = {
            "case_id": case_id_label,
            "runtime_case_id": runtime_case_id,
            "scenario": scenario,
            "case_type": case.get("case_type") or case.get("kind") or "",
            "expected": expected,
            "workflow_decision": workflow_decision,
            "passed": not any(issue["severity"] in {"CRITICAL", "HIGH"} for issue in issues),
            "local_passed": local_accuracy,
            "gemini_passed": gemini_accuracy,
            "documents_count": len(documents),
            "evidence_count": len(context.evidence),
            "evidence_quality": [_jsonable(item) for item in evidence_quality],
            "clinical_facts": clinical_facts,
            "conflict_detection": conflicts,
            "local_review": {
                "available": local.available,
                "used_ai": local.used_ai,
                "error": local.error,
                "output": _review_summary(local.review),
            },
            "gemini_review": {
                "available": gemini.available,
                "used_ai": gemini.used_ai,
                "error": gemini.error,
                "output": _review_summary(gemini.review),
            },
            "governed_review": {
                "used_ai": governed_review.used_ai,
                "approved_set": _jsonable(governed_review.approved_set),
                "explanation": _jsonable(governed_review.explanation),
                "output": _review_summary(governed_review_result),
            },
            "review_comparison": comparison,
            "divergence": divergence,
            "appeal": appeal,
            "governance": {
                "settings": settings.model_dump(mode="json"),
                "compliance": compliance_report,
                "export": export,
            },
            "explainability": {
                "traceability_chain": traceability_chain,
            },
            "issues": sorted(issues, key=lambda item: _severity_rank(item["severity"])),
        }
        return result
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=8)
        return {
            "case_id": case_id_label,
            "scenario": scenario,
            "expected": case.get("expected", {}),
            "workflow_decision": "ERROR",
            "passed": False,
            "local_passed": False,
            "gemini_passed": None,
            "issues": [
                _make_issue(
                    subsystem="Harness",
                    severity="CRITICAL",
                    message=f"Case execution failed: {type(exc).__name__}: {exc}",
                    root_cause="Unhandled workflow exception in validation harness.",
                    case_id=case_id_label,
                    scenario=scenario,
                    extra={"traceback": tb},
                )
            ],
        }
    finally:
        service.close()


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for row in results if row.get("passed"))
    failed = total - passed
    local_expected = [row for row in results if row.get("local_passed") is not None]
    gemini_expected = [row for row in results if row.get("gemini_passed") is not None]
    human_expected = [
        row for row in results
        if bool((row.get("expected") or {}).get("human_review"))
        or row.get("expected", {}).get("recommendation") == "HUMAN_REVIEW"
    ]
    human_ok = [
        row for row in human_expected if row.get("workflow_decision") == "HUMAN_REVIEW"
    ]
    conflict_cases = [
        row for row in results
        if (row.get("conflict_detection") or {}).get("has_conflicts")
    ]
    conflict_ok = [
        row for row in conflict_cases
        if (row.get("conflict_detection") or {}).get("requires_human_review")
    ]
    traceability_ok = [
        row for row in results
        if not any(issue.get("subsystem") == "Evidence Traceability" for issue in row.get("issues", []))
    ]
    governance_ok = [
        row for row in results
        if not any(issue.get("subsystem") in {"Governance", "Human Review Escalation"} for issue in row.get("issues", []))
    ]
    return {
        "total_cases": total,
        "passed": passed,
        "failed": failed,
        "pass_percentage": _pct(passed, total),
        "backend_performance": {
            "local_accuracy": _pct(
                sum(1 for row in local_expected if row.get("local_passed") is True),
                len(local_expected),
            ),
            "gemini_accuracy": _pct(
                sum(1 for row in gemini_expected if row.get("gemini_passed") is True),
                len(gemini_expected),
            ),
            "gemini_cases_run": len(gemini_expected),
            "gemini_cases_unavailable": sum(
                1 for row in results
                if not ((row.get("gemini_review") or {}).get("available"))
            ),
        },
        "safety_metrics": {
            "human_review_compliance": _pct(len(human_ok), len(human_expected)),
            "human_review_expected_cases": len(human_expected),
            "conflict_detection_success_rate": _pct(len(conflict_ok), len(conflict_cases)),
            "conflict_cases": len(conflict_cases),
            "traceability_success_rate": _pct(len(traceability_ok), total),
            "governance_compliance_rate": _pct(len(governance_ok), total),
        },
    }


def _subsystem_failures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in results:
        failures.extend(row.get("issues", []))
    return sorted(failures, key=lambda item: (_severity_rank(item["severity"]), item["subsystem"], item["case_id"]))


def _what_is_working(results: list[dict[str, Any]]) -> dict[str, list[str]]:
    working: dict[str, list[str]] = {
        "Extraction": [],
        "Assembly": [],
        "ClinicalFact": [],
        "Conflict Detection": [],
        "Clinical Review": [],
        "Appeals": [],
        "Governance": [],
        "Explainability": [],
    }
    if any(row.get("evidence_count", 0) > 0 for row in results):
        working["Extraction"].append("Source-backed evidence references were extracted.")
    if any(row.get("documents_count", 0) > 1 for row in results):
        working["Assembly"].append("Multi-document cases assembled into unified contexts.")
    if all((row.get("clinical_facts") or {}).get("facts") for row in results if row.get("workflow_decision") != "ERROR"):
        working["ClinicalFact"].append("ClinicalFact records were generated for executed cases.")
    if any((row.get("conflict_detection") or {}).get("requires_human_review") for row in results):
        working["Conflict Detection"].append("Semantic conflicts routed to human review.")
    if any((row.get("local_review") or {}).get("output") for row in results):
        working["Clinical Review"].append("Local review produced structured criterion-level outputs.")
    if any((row.get("appeal") or {}).get("outcome") == "GENERATED" for row in results):
        working["Appeals"].append("Appeals generated and verification metadata was attached.")
    if any((row.get("governance") or {}).get("compliance") for row in results):
        working["Governance"].append("Governance compliance and export gates executed.")
    if any(((row.get("explainability") or {}).get("traceability_chain")) for row in results):
        working["Explainability"].append("Traceability chains were generated.")
    return {key: value or ["No successful signal captured in this run."] for key, value in working.items()}


def _backend_comparisons(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons = []
    for row in results:
        if row.get("divergence"):
            comparisons.append(row["divergence"])
    return comparisons


def _write_json_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _issue_snippet(issue: dict[str, Any]) -> str:
    return json.dumps(
        {
            key: issue.get(key)
            for key in (
                "subsystem",
                "severity",
                "message",
                "root_cause_hypothesis",
                "recommended_fix",
                "fix_complexity",
            )
        },
        indent=2,
    )


def _write_markdown_report(path: Path, payload: dict[str, Any]) -> None:
    metrics = payload["metrics"]
    results = payload["case_results"]
    failures = payload["subsystem_failures"]
    working = payload["what_is_working"]

    lines: list[str] = []
    lines.append("# HealthAI Master Clinical Validation Report")
    lines.append("")
    lines.append(f"Generated: {payload['generated_at']}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- Total Cases: {metrics['total_cases']}")
    lines.append(f"- Passed: {metrics['passed']}")
    lines.append(f"- Failed: {metrics['failed']}")
    lines.append(f"- Pass Percentage: {_format_pct(metrics['pass_percentage'])}")
    lines.append("")
    lines.append("### Backend Performance")
    lines.append("")
    backend = metrics["backend_performance"]
    lines.append(f"- Local Accuracy: {_format_pct(backend['local_accuracy'])}")
    lines.append(f"- Gemini Accuracy: {_format_pct(backend['gemini_accuracy'])}")
    lines.append(f"- Gemini Cases Run: {backend['gemini_cases_run']}")
    lines.append(f"- Gemini Cases Unavailable/Skipped: {backend['gemini_cases_unavailable']}")
    lines.append("")
    lines.append("### Safety Metrics")
    lines.append("")
    safety = metrics["safety_metrics"]
    lines.append(f"- Human Review Compliance: {_format_pct(safety['human_review_compliance'])}")
    lines.append(f"- Conflict Detection Success Rate: {_format_pct(safety['conflict_detection_success_rate'])}")
    lines.append(f"- Traceability Success Rate: {_format_pct(safety['traceability_success_rate'])}")
    lines.append(f"- Governance Compliance Rate: {_format_pct(safety['governance_compliance_rate'])}")
    lines.append("")
    lines.append("## Per-Case Results")
    lines.append("")
    for row in results:
        expected = row.get("expected") or {}
        local = ((row.get("local_review") or {}).get("output") or {}).get("recommendation")
        gemini_output = (row.get("gemini_review") or {}).get("output")
        gemini = (gemini_output or {}).get("recommendation") if gemini_output else "UNAVAILABLE"
        appeal = (row.get("appeal") or {}).get("outcome", "SKIPPED")
        lines.append(f"### {row['case_id']} - {row['scenario']}")
        lines.append("")
        lines.append(f"- Expected Outcome: {expected.get('recommendation', '(not specified)')}")
        lines.append(f"- Local Outcome: {local or row.get('workflow_decision')}")
        lines.append(f"- Gemini Outcome: {gemini}")
        lines.append(f"- Appeal Outcome: {appeal}")
        lines.append(f"- Workflow Decision: {row.get('workflow_decision')}")
        lines.append(f"- Pass/Fail: {'PASS' if row.get('passed') else 'FAIL'}")
        lines.append("- Issues Found:")
        issues = row.get("issues") or []
        if not issues:
            lines.append("  - None")
        else:
            for issue in issues:
                lines.append(
                    f"  - [{issue['severity']}] {issue['subsystem']}: {issue['message']}"
                )
            lines.append("")
            lines.append("Relevant JSON snippet:")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(issues[:3], indent=2))
            lines.append("```")
        lines.append("")
    lines.append("## What Is Working")
    lines.append("")
    for subsystem, items in working.items():
        lines.append(f"### {subsystem}")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    lines.append("## What Needs Work")
    lines.append("")
    if not failures:
        lines.append("No subsystem failures were detected in this run.")
    else:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for issue in failures:
            grouped.setdefault(issue["subsystem"], []).append(issue)
        for subsystem, issues in grouped.items():
            lines.append(f"### {subsystem}")
            for issue in issues:
                lines.append(f"- Severity: {issue['severity']}")
                lines.append(f"  - Case: {issue['case_id']} - {issue['scenario']}")
                lines.append(f"  - Root Cause Hypothesis: {issue['root_cause_hypothesis']}")
                lines.append(f"  - Recommended Fix: {issue['recommended_fix']}")
                lines.append(f"  - Fix Complexity: {issue['fix_complexity']}")
                lines.append("  - Failure JSON:")
                lines.append("")
                lines.append("```json")
                lines.append(_issue_snippet(issue))
                lines.append("```")
            lines.append("")
    lines.append("## AI vs Local Divergence Analysis")
    lines.append("")
    comparisons = payload["backend_comparisons"]
    if not comparisons:
        lines.append("No Local/Gemini recommendation divergence was captured.")
    else:
        for item in comparisons:
            lines.append(f"### {item['case_id']} - {item['scenario']}")
            lines.append(f"- Likely Root Cause: {item['likely_root_cause']}")
            lines.append(f"- Recommended Investigation: {item['recommended_investigation']}")
            lines.append("")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run_suite(
    *,
    matrix_path: Path,
    json_report_path: Path,
    md_report_path: Path,
    gemini_mode: str,
    settings: GovernanceSettings,
) -> dict[str, Any]:
    cases = _read_matrix(matrix_path)
    print(f"[validation] Loaded {len(cases)} case(s) from {matrix_path if matrix_path.exists() else 'starter matrix'}")
    results = [
        run_case(case, gemini_mode=gemini_mode, settings=settings)
        for case in cases
    ]
    payload = {
        "generated_at": _utc_now_iso(),
        "matrix_path": str(matrix_path),
        "gemini_mode": gemini_mode,
        "architecture_summary": {
            "graphify_used": True,
            "execution_boundary": "CaseService + production engines",
            "reused_infrastructure": [
                "app.validation.runner discovery",
                "CaseService",
                "CaseAssemblyEngine",
                "ClinicalReviewEngine",
                "GuidelineReviewAgent",
                "ExplainabilityService",
                "GovernanceService",
                "AppealVerifier",
                "SafetyGate",
            ],
            "note": (
                "Existing ValidationRunner remains the lightweight payer-pack "
                "checker; this harness adds full workflow and architecture "
                "weakness reporting."
            ),
        },
        "metrics": _metrics(results),
        "case_results": results,
        "subsystem_failures": _subsystem_failures(results),
        "backend_comparisons": _backend_comparisons(results),
        "what_is_working": _what_is_working(results),
        "governance_findings": [
            issue for issue in _subsystem_failures(results)
            if issue["subsystem"] in {"Governance", "Human Review Escalation"}
        ],
    }
    _write_json_report(json_report_path, payload)
    _write_markdown_report(md_report_path, payload)
    print(f"[validation] Wrote JSON report: {json_report_path}")
    print(f"[validation] Wrote Markdown report: {md_report_path}")
    print(
        "[validation] Summary: "
        f"{payload['metrics']['passed']}/{payload['metrics']['total_cases']} passed "
        f"({_format_pct(payload['metrics']['pass_percentage'])})"
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the HealthAI master clinical validation suite."
    )
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX))
    parser.add_argument("--json-out", default=str(DEFAULT_JSON_REPORT))
    parser.add_argument("--md-out", default=str(DEFAULT_MD_REPORT))
    parser.add_argument(
        "--gemini",
        choices=("auto", "skip", "force"),
        default="auto",
        help="Gemini execution mode. auto runs when available; skip disables; force surfaces initialization errors.",
    )
    parser.add_argument(
        "--validated-mode",
        action="store_true",
        help="Run governance in validated evidence mode.",
    )
    parser.add_argument(
        "--allow-autonomous-denials",
        action="store_true",
        help="Do not route deterministic denials to human review solely because they are denials.",
    )
    args = parser.parse_args(argv)

    settings = GovernanceSettings(
        validated_evidence_mode=args.validated_mode,
        allow_unreviewed_evidence=True,
        minimum_quality_score=0.0,
        require_conflict_resolution=True,
        require_human_review_before_export=True,
        block_autonomous_denials=not args.allow_autonomous_denials,
        require_verified_appeal_claims=True,
    )

    payload = run_suite(
        matrix_path=Path(args.matrix),
        json_report_path=Path(args.json_out),
        md_report_path=Path(args.md_out),
        gemini_mode=args.gemini,
        settings=settings,
    )
    return 0 if payload["metrics"]["failed"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
