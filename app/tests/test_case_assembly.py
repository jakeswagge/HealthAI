"""Tests for the CaseAssemblyEngine: multi-doc, conflicts, missing info."""

from __future__ import annotations

from app.assembly.engine import CaseAssemblyEngine
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.conflict_report import ConflictSeverity


def _doc(filename, dt, text) -> CaseDocument:
    return CaseDocument(case_id="C1", filename=filename, document_type=dt, raw_text=text)


DENIAL = _doc(
    "denial.txt",
    DocumentCategory.DENIAL_LETTER,
    "Member Name: Harold Greene\nMember ID: WP-558210334\n"
    "Diagnosis: Rheumatoid arthritis\nProcedure: Humira (adalimumab)\n"
    "ICD-10: M06.9\nStatus: DENIED\n"
    "Reason for Denial: Step therapy not met; no DMARD trial documented.",
)
NOTE = _doc(
    "note.txt",
    DocumentCategory.CLINICAL_NOTE,
    "Patient: Harold Greene\nDiagnosis: Rheumatoid arthritis\n"
    "History: Completed methotrexate (DMARD) trial with failure. Negative TB screen.",
)
LAB = _doc(
    "lab.txt",
    DocumentCategory.LAB_RESULT,
    "Patient: Harold Greene\nMember ID: WP-558210334\n"
    "Rheumatoid Factor: 85 IU/mL HIGH\nQuantiFERON-TB Gold: NEGATIVE",
)


class TestMultiDocumentAssembly:
    def test_combines_evidence_from_all_docs(self):
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, NOTE, LAB])
        assert len(ctx.document_ids) == 3
        source_files = {e.source_filename for e in ctx.evidence}
        assert {"denial.txt", "note.txt", "lab.txt"} <= source_files

    def test_resolved_patient_case_built(self):
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, NOTE, LAB])
        pc = ctx.patient_case
        assert pc.member_id == "WP-558210334"
        assert pc.requested_service is not None
        assert pc.decision.value == "denied"
        # Field source attribution present.
        assert "member_id" in pc.field_sources
        assert pc.field_sources["member_id"].source_page == 1

    def test_no_conflicts_when_consistent(self):
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, NOTE, LAB])
        # diagnosis agrees (RA) across denial + note.
        dx_conflicts = [c for c in ctx.conflict_report.conflicts if c.fact_type == "diagnosis"]
        assert dx_conflicts == []

    def test_evidence_inventory_serializable(self):
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, NOTE, LAB])
        inv = ctx.inventory()
        assert inv and all("citation" in row for row in inv)


class TestConflictDetection:
    def test_diagnosis_conflict_is_high(self):
        note_conflict = _doc(
            "note.txt", DocumentCategory.CLINICAL_NOTE,
            "Patient: Harold Greene\nDiagnosis: Osteoarthritis",
        )
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, note_conflict])
        dx = [c for c in ctx.conflict_report.conflicts if c.fact_type == "diagnosis"]
        assert dx
        assert dx[0].severity is ConflictSeverity.HIGH
        assert len(dx[0].values) >= 2

    def test_member_id_conflict_is_high(self):
        other = _doc(
            "form.txt", DocumentCategory.PRIOR_AUTH_FORM,
            "Member ID: WP-999999999\nProcedure: Humira",
        )
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, other])
        mid = [c for c in ctx.conflict_report.conflicts if c.fact_type == "member_id"]
        assert mid and mid[0].severity is ConflictSeverity.HIGH

    def test_requested_service_conflict_is_medium(self):
        other = _doc(
            "form.txt", DocumentCategory.PRIOR_AUTH_FORM,
            "Member ID: WP-558210334\nProcedure: Enbrel (etanercept)",
        )
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, other])
        svc = [c for c in ctx.conflict_report.conflicts if c.fact_type == "requested_service"]
        assert svc and svc[0].severity is ConflictSeverity.MEDIUM

    def test_report_highest_severity(self):
        note_conflict = _doc(
            "note.txt", DocumentCategory.CLINICAL_NOTE,
            "Diagnosis: Osteoarthritis",
        )
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, note_conflict])
        assert ctx.conflict_report.has_conflicts
        assert ctx.conflict_report.highest_severity is ConflictSeverity.HIGH


class TestMissingInformation:
    def test_missing_required_fields_flagged(self):
        sparse = _doc("misc.txt", DocumentCategory.OTHER, "Some unrelated text.")
        ctx = CaseAssemblyEngine().assemble("C1", [sparse])
        joined = " ".join(ctx.missing_information).lower()
        assert "patient_name" in joined
        assert "member_id" in joined

    def test_no_missing_when_complete(self):
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, NOTE, LAB])
        # denial provides name, member_id, diagnosis, requested_service.
        assert ctx.missing_information == []


class TestAuthoritativeResolution:
    def test_diagnosis_prefers_clinical_note(self):
        # Denial says RA, imaging says something else -> clinical note wins for dx.
        denial = _doc("denial.txt", DocumentCategory.DENIAL_LETTER, "Diagnosis: Generic arthritis\nMember ID: WP-1")
        note = _doc("note.txt", DocumentCategory.CLINICAL_NOTE, "Diagnosis: Seropositive rheumatoid arthritis")
        ctx = CaseAssemblyEngine().assemble("C1", [denial, note])
        rf = ctx.resolved_facts["diagnosis"]
        assert rf.source_filename == "note.txt"

    def test_denial_reason_prefers_denial_letter(self):
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL, NOTE])
        rf = ctx.resolved_facts.get("denial_reason")
        assert rf is not None
        assert rf.source_filename == "denial.txt"
