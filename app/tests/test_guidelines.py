"""Tests for the clinical guideline model and repository."""

from __future__ import annotations

from app.guidelines.repository import GuidelineRepository, get_default_repository
from app.models.clinical_guideline import ClinicalGuideline
from app.models.patient_case import PatientCase


class TestRepositoryLoading:
    def test_default_repo_loads_five_guidelines(self):
        repo = get_default_repository(force_reload=True)
        assert len(repo) == 5
        ids = {g.guideline_id for g in repo.all()}
        assert "GL-HUMIRA-001" in ids
        assert "GL-ENBREL-001" in ids
        assert "GL-MRI-LUMBAR-001" in ids
        assert "GL-CT-CHEST-001" in ids
        assert "GL-PT-001" in ids

    def test_all_guidelines_have_required_schema(self):
        repo = get_default_repository()
        for g in repo.all():
            assert isinstance(g, ClinicalGuideline)
            assert g.guideline_id
            assert g.service_name
            assert g.diagnosis
            assert g.version
            assert g.source
            assert g.required_criteria  # non-empty

    def test_missing_directory_returns_empty(self, tmp_path):
        repo = GuidelineRepository.from_directory(tmp_path / "nope")
        assert len(repo) == 0

    def test_bad_json_is_skipped(self, tmp_path):
        (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
        good = {
            "guideline_id": "GL-X",
            "service_name": "X",
            "diagnosis": "Y",
            "version": "1",
            "source": "S",
            "required_criteria": [
                {"id": "C1", "description": "c", "keywords": ["k"], "required": True}
            ],
        }
        import json

        (tmp_path / "good.json").write_text(json.dumps(good), encoding="utf-8")
        repo = GuidelineRepository.from_directory(tmp_path)
        assert len(repo) == 1
        assert repo.get("GL-X") is not None


class TestMatching:
    def test_matches_by_service_name(self):
        repo = get_default_repository()
        case = PatientCase(requested_service="Humira (adalimumab)")
        match = repo.match(case)
        assert match is not None
        assert match.guideline.guideline_id == "GL-HUMIRA-001"

    def test_matches_by_cpt(self):
        repo = get_default_repository()
        case = PatientCase(requested_service="advanced imaging", cpt_codes=["72148"])
        match = repo.match(case)
        assert match is not None
        assert match.guideline.guideline_id == "GL-MRI-LUMBAR-001"

    def test_matches_mri_lumbar_alias(self):
        repo = get_default_repository()
        case = PatientCase(requested_service="Lumbar spine MRI")
        match = repo.match(case)
        assert match.guideline.guideline_id == "GL-MRI-LUMBAR-001"

    def test_no_match_returns_none(self):
        repo = get_default_repository()
        case = PatientCase(requested_service="dental cleaning")
        match = repo.match(case)
        assert match is None
