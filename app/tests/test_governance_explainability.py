"""Tests for Milestone 13: governance-enforced reviews/appeals + explainability.

Proves the trust contract:
- In VALIDATED mode, generated reviews/appeals use ONLY approved evidence.
- Rejected evidence never appears in the used-evidence lineage and never
  influences the recommendation/appeal.
- Draft mode and validated mode produce different evidence usage.
- Explainability chains are accurate (used + excluded == all evidence).
- Exports include the new explainability/traceability files.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.cases.export import build_export_files, build_export_zip
from app.cases.service import CaseService
from app.models.audit_event import AuditEventType
from app.models.evidence_review_decision import EvidenceDecision
from app.models.governance import EvidenceMode, GovernanceSettings
from app.storage.database import connect, initialize_schema


DENIAL = (
    "Member Name: Harold Greene\nMember ID: WP-558210334\n"
    "Diagnosis: Rheumatoid arthritis\nProcedure: Humira (adalimumab)\n"
    "ICD-10: M06.9\nStatus: DENIED\n"
    "Reason for Denial: Step therapy not met; no DMARD trial documented."
)
NOTE = "Patient: Harold Greene\nDiagnosis: Osteoarthritis"
LAB = "Member ID: WP-558210334\nRheumatoid Factor: 85 HIGH"


@pytest.fixture
def conn():
    c = connect(":memory:")
    initialize_schema(c)
    yield c
    c.close()


@pytest.fixture
def service(conn):
    return CaseService(conn=conn)


def _scored_case(service):
    rec = service.create_case("gov case")
    service.ingest_document(rec.case_id, "denial.png", DENIAL.encode())
    service.ingest_document(rec.case_id, "note.png", NOTE.encode())
    service.ingest_document(rec.case_id, "lab.png", LAB.encode())
    service.assemble_case(rec.case_id)
    service.score_evidence(rec.case_id)
    return rec.case_id


def _validated(**kw) -> GovernanceSettings:
    kw.setdefault("validated_evidence_mode", True)
    return GovernanceSettings(**kw)


# --------------------------------------------------------------------------- #
# Governance-enforced review
# --------------------------------------------------------------------------- #
class TestGovernedReview:
    def test_draft_review_uses_all_evidence(self, service):
        case_id = _scored_case(service)
        governed = service.generate_governed_review(case_id, GovernanceSettings())
        all_ids = {e.evidence_id for e in service.list_evidence(case_id)}
        used_ids = set(governed.explanation.evidence_used_ids)
        assert governed.explanation.governance_mode is EvidenceMode.DRAFT
        assert used_ids == all_ids
        assert not governed.explanation.evidence_excluded

    def test_validated_review_excludes_rejected(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        rejected_id = evs[0].evidence_id
        service.record_evidence_decision(case_id, rejected_id, "Rev", EvidenceDecision.REJECT)

        governed = service.generate_governed_review(case_id, _validated(allow_unreviewed_evidence=True))
        used_ids = set(governed.explanation.evidence_used_ids)
        excluded_ids = set(governed.explanation.evidence_excluded_ids)

        assert governed.explanation.governance_mode is EvidenceMode.VALIDATED
        assert rejected_id not in used_ids
        assert rejected_id in excluded_ids

    def test_rejected_evidence_never_influences_recommendation(self, service):
        """Rejecting all diagnosis-conflicting evidence cannot leak into review."""
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        # Reject every piece of evidence: validated review must use none of it.
        for e in evs:
            service.record_evidence_decision(case_id, e.evidence_id, "Rev", "REJECT")

        governed = service.generate_governed_review(case_id, _validated(allow_unreviewed_evidence=True))
        assert governed.explanation.evidence_used == []
        # Every reference is excluded.
        assert len(governed.explanation.evidence_excluded) == len(evs)

    def test_validated_review_audited(self, service):
        case_id = _scored_case(service)
        service.generate_governed_review(case_id, _validated())
        types = [e.event_type for e in service.history(case_id)]
        assert AuditEventType.REVIEW_EXPLANATION_GENERATED in types


# --------------------------------------------------------------------------- #
# Governance-enforced appeal
# --------------------------------------------------------------------------- #
class TestGovernedAppeal:
    def test_validated_appeal_excludes_rejected(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        rejected_id = evs[0].evidence_id
        service.record_evidence_decision(case_id, rejected_id, "Rev", "REJECT")

        governed = service.generate_governed_appeal(case_id, _validated(allow_unreviewed_evidence=True))
        used_ids = set(governed.explanation.evidence_used_ids)
        assert rejected_id not in used_ids
        assert rejected_id in set(governed.explanation.evidence_excluded_ids)

    def test_appeal_explanation_audited(self, service):
        case_id = _scored_case(service)
        service.generate_governed_appeal(case_id, _validated())
        types = [e.event_type for e in service.history(case_id)]
        assert AuditEventType.APPEAL_EXPLANATION_GENERATED in types

    def test_draft_vs_validated_appeal_evidence_differs(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "REJECT")

        draft = service.generate_governed_appeal(case_id, GovernanceSettings())
        validated = service.generate_governed_appeal(case_id, _validated(allow_unreviewed_evidence=True))
        assert len(validated.explanation.evidence_used) < len(draft.explanation.evidence_used)


# --------------------------------------------------------------------------- #
# Explainability chain accuracy
# --------------------------------------------------------------------------- #
class TestExplainabilityChains:
    def test_used_plus_excluded_equals_all(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "REJECT")

        governed = service.generate_governed_review(case_id, _validated(allow_unreviewed_evidence=True))
        used = set(governed.explanation.evidence_used_ids)
        excluded = set(governed.explanation.evidence_excluded_ids)
        all_ids = {e.evidence_id for e in evs}
        assert used | excluded == all_ids
        assert used & excluded == set()

    def test_lineage_carries_source_and_decision(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "APPROVE")

        chain = service.traceability_chain(case_id, _validated(allow_unreviewed_evidence=True))
        by_id = {link.evidence_id: link for link in chain.links}
        link = by_id[evs[0].evidence_id]
        assert link.reviewer_decision == "APPROVE"
        assert link.source_document_id
        # Quality was scored, so a score is present.
        assert link.quality_score is not None

    def test_excluded_lineage_has_reason(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "REJECT")

        chain = service.traceability_chain(case_id, _validated(allow_unreviewed_evidence=True))
        excluded = [link for link in chain.links if not link.included]
        assert excluded
        assert all(link.exclusion_reason for link in excluded)

    def test_traceability_chain_mode_reflects_settings(self, service):
        case_id = _scored_case(service)
        draft_chain = service.traceability_chain(case_id, GovernanceSettings())
        validated_chain = service.traceability_chain(case_id, _validated())
        assert draft_chain.governance_mode is EvidenceMode.DRAFT
        assert validated_chain.governance_mode is EvidenceMode.VALIDATED


# --------------------------------------------------------------------------- #
# Explanation helpers on the facade
# --------------------------------------------------------------------------- #
class TestExplainHelpers:
    def test_explain_review_matches_consumption(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "REJECT")
        settings = _validated(allow_unreviewed_evidence=True)

        governed = service.generate_governed_review(case_id, settings)
        exp = service.explain_review(case_id, governed.review, settings)
        # Same evidence split as the governed run.
        assert set(exp.evidence_used_ids) == set(governed.explanation.evidence_used_ids)

    def test_explain_appeal_used_ids_subset_of_consumption(self, service):
        case_id = _scored_case(service)
        settings = _validated()
        governed = service.generate_governed_appeal(case_id, settings)
        used, aset = service.evidence_for_consumption(case_id, settings)
        consumption_ids = {e.evidence_id for e in used}
        assert set(governed.explanation.evidence_used_ids) == consumption_ids


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
class TestExplainabilityExports:
    def test_export_files_include_explanations(self, service):
        case_id = _scored_case(service)
        settings = _validated()
        record = service.get_case(case_id)

        review_exp = service.generate_governed_review(case_id, settings).explanation
        appeal_exp = service.generate_governed_appeal(case_id, settings).explanation
        chain = service.traceability_chain(case_id, settings)

        files = build_export_files(
            record,
            service.history(case_id),
            evidence=service.list_evidence(case_id),
            review_explanation=review_exp,
            appeal_explanation=appeal_exp,
            traceability_chain=chain,
        )
        assert "review_explanation.json" in files
        assert "appeal_explanation.json" in files
        assert "traceability_chain.json" in files

        # The traceability file is parseable and carries lineage links.
        chain_data = json.loads(files["traceability_chain.json"])
        assert chain_data["case_id"] == case_id
        assert chain_data["links"]

    def test_export_zip_contains_explanations(self, service):
        case_id = _scored_case(service)
        settings = _validated()
        record = service.get_case(case_id)
        chain = service.traceability_chain(case_id, settings)
        review_exp = service.generate_governed_review(case_id, settings).explanation

        data = build_export_zip(
            record,
            service.history(case_id),
            evidence=service.list_evidence(case_id),
            review_explanation=review_exp,
            traceability_chain=chain,
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
        assert {"review_explanation.json", "traceability_chain.json"} <= names

    def test_backward_compatible_export_without_explanations(self, service):
        case_id = _scored_case(service)
        record = service.get_case(case_id)
        files = build_export_files(record, service.history(case_id))
        assert "review_explanation.json" not in files
        assert "appeal_explanation.json" not in files
        assert "traceability_chain.json" not in files


# --------------------------------------------------------------------------- #
# Success criteria: case contains approved/rejected/flagged + outputs differ
# --------------------------------------------------------------------------- #
class TestSuccessCriteria:
    def test_case_has_approved_rejected_flagged(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        assert len(evs) >= 3
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "APPROVE")
        service.record_evidence_decision(case_id, evs[1].evidence_id, "Rev", "REJECT")
        service.record_evidence_decision(case_id, evs[2].evidence_id, "Rev", "FLAG")

        chain = service.traceability_chain(case_id, _validated(allow_unreviewed_evidence=True))
        decisions = {link.evidence_id: link.reviewer_decision for link in chain.links}
        assert decisions[evs[0].evidence_id] == "APPROVE"
        assert decisions[evs[1].evidence_id] == "REJECT"
        assert decisions[evs[2].evidence_id] == "FLAG"

    def test_draft_and_validated_outputs_differ(self, service):
        case_id = _scored_case(service)
        evs = service.list_evidence(case_id)
        service.record_evidence_decision(case_id, evs[0].evidence_id, "Rev", "REJECT")

        draft = service.generate_governed_review(case_id, GovernanceSettings())
        validated = service.generate_governed_review(case_id, _validated(allow_unreviewed_evidence=True))
        assert len(validated.explanation.evidence_used) < len(draft.explanation.evidence_used)
