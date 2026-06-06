"""Tests for the Final Milestone: payer packs, operational health, validation.

Covers: payer selection, guideline switching, review/appeal behavior under
packs (provenance recorded), operational reporting, the validation runner, and
export generation for the new files.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.cases.export import build_export_files, build_export_zip
from app.cases.service import CaseService
from app.models.governance import GovernanceSettings
from app.models.payer import PayerProfile, PayerStatus
from app.payers.packs import GuidelinePackResolver, get_pack_resolver
from app.payers.repository import PayerRepository, get_payer_repository
from app.storage.database import connect, initialize_schema
from app.validation.runner import ValidationRunner, load_default_scenarios


DENIAL = (
    "Member Name: Harold Greene\nMember ID: WP-558210334\n"
    "Diagnosis: Rheumatoid arthritis\nProcedure: Humira (adalimumab)\n"
    "ICD-10: M06.9\nStatus: DENIED\n"
    "Reason for Denial: Step therapy not met; no DMARD trial documented."
)
NOTE = "Patient: Harold Greene\nDiagnosis: Osteoarthritis"


@pytest.fixture
def conn():
    c = connect(":memory:")
    initialize_schema(c)
    yield c
    c.close()


@pytest.fixture
def service(conn):
    return CaseService(conn=conn)


def _case(service):
    rec = service.create_case("payer case")
    service.ingest_document(rec.case_id, "denial.png", DENIAL.encode())
    service.ingest_document(rec.case_id, "note.png", NOTE.encode())
    service.assemble_case(rec.case_id)
    service.score_evidence(rec.case_id)
    return rec.case_id


# --------------------------------------------------------------------------- #
# Payer repository + profiles
# --------------------------------------------------------------------------- #
class TestPayerRepository:
    def test_default_always_present(self):
        repo = PayerRepository()
        assert repo.get("DEFAULT") is not None

    def test_loads_profiles_from_disk(self):
        repo = get_payer_repository(force_reload=True)
        ids = {p.payer_id for p in repo.all()}
        assert {"DEFAULT", "AETNA", "UNITEDHEALTHCARE", "CIGNA", "HUMANA", "MOCK_PAYER"} <= ids

    def test_get_or_default_falls_back(self):
        repo = PayerRepository()
        assert repo.get_or_default("NOPE").payer_id == "DEFAULT"

    def test_case_insensitive_lookup(self):
        repo = get_payer_repository(force_reload=True)
        assert repo.get("aetna") is not None

    def test_profile_status_coercion(self):
        p = PayerProfile(payer_id="X", payer_name="X", status="active")
        assert p.status is PayerStatus.ACTIVE
        assert p.is_active


# --------------------------------------------------------------------------- #
# Guideline pack resolver
# --------------------------------------------------------------------------- #
class TestGuidelinePacks:
    def test_available_packs_include_known(self):
        resolver = get_pack_resolver(force_reload=True)
        packs = set(resolver.available_packs())
        assert "DEFAULT" in packs
        assert {"AETNA", "UNITEDHEALTHCARE", "MOCK_PAYER"} <= packs

    def test_default_pack_matches_base_library(self):
        resolver = GuidelinePackResolver()
        repo = resolver.resolve("DEFAULT")
        g = repo.get("GL-HUMIRA-001")
        assert g is not None
        assert g.version == "2026.1"

    def test_pack_overrides_base_guideline(self):
        resolver = GuidelinePackResolver()
        aetna = resolver.resolve("AETNA").get("GL-HUMIRA-001")
        default = resolver.resolve("DEFAULT").get("GL-HUMIRA-001")
        assert aetna.version != default.version
        # Aetna pack is stricter (more required criteria).
        assert aetna.required_count() > default.required_count()

    def test_unknown_pack_falls_back_to_default(self):
        resolver = GuidelinePackResolver()
        repo = resolver.resolve("DOES_NOT_EXIST")
        assert repo.get("GL-HUMIRA-001").version == "2026.1"


# --------------------------------------------------------------------------- #
# Payer-aware reviews / appeals
# --------------------------------------------------------------------------- #
class TestPayerReviews:
    def test_review_records_payer_provenance(self, service):
        case_id = _case(service)
        pr = service.review_with_payer(case_id, "AETNA")
        r = pr.review
        assert r.payer_id == "AETNA"
        assert r.guideline_pack == "AETNA"
        assert r.guideline_version == "AETNA-2026.1"

    def test_appeal_records_payer_provenance(self, service):
        case_id = _case(service)
        pa = service.appeal_with_payer(case_id, "UNITEDHEALTHCARE")
        a = pa.appeal
        assert a.payer_id == "UNITEDHEALTHCARE"
        assert a.guideline_pack == "UNITEDHEALTHCARE"
        assert a.guideline_version == "UHC-2026.1"

    def test_guideline_switching_changes_pack(self, service):
        case_id = _case(service)
        default = service.review_with_payer(case_id, "DEFAULT").review
        aetna = service.review_with_payer(case_id, "AETNA").review
        assert default.guideline_pack == "DEFAULT"
        assert aetna.guideline_pack == "AETNA"
        assert default.guideline_version != aetna.guideline_version

    def test_same_case_three_payers_pack_aware(self, service):
        case_id = _case(service)
        out = {}
        for pid in ("DEFAULT", "AETNA", "UNITEDHEALTHCARE"):
            r = service.review_with_payer(case_id, pid).review
            out[pid] = (r.guideline_pack, r.guideline_version, r.recommendation.value)
        # Distinct packs/versions per payer.
        assert out["DEFAULT"][1] != out["AETNA"][1] != out["UNITEDHEALTHCARE"][1]
        assert out["AETNA"][0] == "AETNA"

    def test_unknown_payer_defaults(self, service):
        case_id = _case(service)
        r = service.review_with_payer(case_id, "MADE_UP").review
        assert r.guideline_pack == "DEFAULT"

    def test_governance_preserved_under_payer(self, service):
        """Rejected evidence is still excluded when reviewing under a payer pack."""
        case_id = _case(service)
        evs = service.list_evidence(case_id)
        # Reject the osteoarthritis diagnosis from the note.
        rej = next(e for e in evs if "osteoarthritis" in e.normalized_fact.lower())
        service.record_evidence_decision(case_id, rej.evidence_id, "Rev", "REJECT")
        settings = GovernanceSettings(validated_evidence_mode=True, allow_unreviewed_evidence=True)
        pr = service.review_with_payer(case_id, "AETNA", settings)
        used_ids = set(pr.governed.explanation.evidence_used_ids)
        assert rej.evidence_id not in used_ids


# --------------------------------------------------------------------------- #
# Operational health
# --------------------------------------------------------------------------- #
class TestOperationalHealth:
    def test_report_generated(self, service):
        _case(service)
        report = service.operational_health()
        assert report.total_cases == 1
        assert report.total_documents >= 2

    def test_conflict_frequency_detected(self, service):
        _case(service)  # denial vs note -> diagnosis conflict
        report = service.operational_health()
        assert report.conflicts_detected >= 1
        assert report.conflict_frequency > 0.0

    def test_healthy_when_no_failures(self, service):
        _case(service)
        report = service.operational_health()
        # No OCR/extraction/review/appeal failures recorded in this flow.
        assert report.total_failures == 0

    def test_as_dict_roundtrip(self, service):
        _case(service)
        d = service.operational_health().as_dict()
        assert "claude_fallback_rate" in d
        assert "conflict_frequency" in d
        assert "is_healthy" in d


# --------------------------------------------------------------------------- #
# Validation runner
# --------------------------------------------------------------------------- #
class TestValidationRunner:
    def test_default_scenarios_load(self):
        scenarios = load_default_scenarios()
        assert scenarios
        assert all("scenario_id" in s for s in scenarios)

    def test_runner_all_pass(self):
        report = ValidationRunner(settings=GovernanceSettings()).run()
        assert report.total > 0
        assert report.all_passed, [r.as_dict() for r in report.results if not r.passed]

    def test_runner_records_pack_versions(self):
        report = ValidationRunner(settings=GovernanceSettings()).run()
        aetna = [r for r in report.results if r.payer_id == "AETNA"]
        assert aetna
        assert all(r.guideline_version.startswith("AETNA") for r in aetna)

    def test_report_as_dict(self):
        report = ValidationRunner(settings=GovernanceSettings()).run()
        d = report.as_dict()
        assert d["total"] == report.total
        assert "results" in d


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
class TestPayerOpsExports:
    def test_export_files_include_new_files(self, service):
        case_id = _case(service)
        record = service.get_case(case_id)
        payer = service.get_payer("AETNA")
        health = service.operational_health()
        report = ValidationRunner(settings=GovernanceSettings()).run().as_dict()

        files = build_export_files(
            record,
            service.history(case_id),
            payer_profile=payer,
            operational_health=health,
            validation_report=report,
        )
        assert "payer_profile.json" in files
        assert "operational_health.json" in files
        assert "validation_report.json" in files

        payer_data = json.loads(files["payer_profile.json"])
        assert payer_data["payer_id"] == "AETNA"

    def test_export_zip_contains_new_files(self, service):
        case_id = _case(service)
        record = service.get_case(case_id)
        data = build_export_zip(
            record,
            service.history(case_id),
            payer_profile=service.get_payer("DEFAULT"),
            operational_health=service.operational_health(),
        )
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
        assert {"payer_profile.json", "operational_health.json"} <= names

    def test_backward_compatible_export(self, service):
        case_id = _case(service)
        record = service.get_case(case_id)
        files = build_export_files(record, service.history(case_id))
        assert "payer_profile.json" not in files
        assert "operational_health.json" not in files
        assert "validation_report.json" not in files
