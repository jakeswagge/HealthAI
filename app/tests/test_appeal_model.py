"""Unit tests for the AppealLetter pydantic model."""

from __future__ import annotations

from app.models.appeal_letter import AppealLetter


class TestDefaultsAndCoercion:
    def test_minimal_construction(self):
        appeal = AppealLetter(appeal_id="APL-1")
        assert appeal.appeal_id == "APL-1"
        assert appeal.created_at  # auto-populated ISO timestamp
        assert appeal.guideline_support == []
        assert appeal.missing_information == []
        assert appeal.confidence_score == 0.0
        assert appeal.has_letter is False

    def test_null_markers_become_none(self):
        appeal = AppealLetter(appeal_id="APL-1", patient_name="  null ")
        assert appeal.patient_name is None

    def test_string_list_coercion(self):
        appeal = AppealLetter(appeal_id="APL-1", guideline_support="single citation")
        assert appeal.guideline_support == ["single citation"]

    def test_list_filters_blanks(self):
        appeal = AppealLetter(
            appeal_id="APL-1",
            missing_information=["a", "", None, "  ", "b"],
        )
        assert appeal.missing_information == ["a", "b"]

    def test_confidence_clamped(self):
        assert AppealLetter(appeal_id="x", confidence_score=5).confidence_score == 1.0
        assert AppealLetter(appeal_id="x", confidence_score=-1).confidence_score == 0.0

    def test_confidence_invalid_becomes_zero(self):
        assert AppealLetter(appeal_id="x", confidence_score="high").confidence_score == 0.0


class TestDerived:
    def test_has_letter_true(self):
        appeal = AppealLetter(appeal_id="x", letter_text="Dear Appeals Dept...")
        assert appeal.has_letter is True

    def test_summary(self):
        appeal = AppealLetter(
            appeal_id="x",
            patient_name="Jane Doe",
            insurance_company="Acme Health",
            requested_service="Humira",
        )
        s = appeal.summary()
        assert "Jane Doe" in s
        assert "Acme Health" in s
        assert "Humira" in s

    def test_to_txt_and_markdown(self):
        appeal = AppealLetter(appeal_id="x", letter_text="# Title\nbody")
        assert appeal.to_txt() == "# Title\nbody"
        assert appeal.to_markdown() == "# Title\nbody"
