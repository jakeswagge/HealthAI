"""Tests for the CaseAssemblyEngine: multi-doc, conflicts, missing info."""

from __future__ import annotations

from app.assembly.engine import CaseAssemblyEngine
from app.models.case_document import CaseDocument, DocumentCategory
from app.models.conflict_report import ConflictSeverity
from app.models.evidence_reference import EvidenceReference
from app.models.review_result import Recommendation
from app.review.engine import ClinicalReviewEngine


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

    def test_primary_diagnosis_conflict_requires_human_review(self):
        ra = _doc(
            "ra.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Diagnosis: Rheumatoid Arthritis",
        )
        psa = _doc(
            "psa.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Diagnosis: Psoriatic Arthritis",
        )

        ctx = CaseAssemblyEngine().assemble("C1", [ra, psa])

        dx = [c for c in ctx.conflict_report.conflicts if c.fact_type == "diagnosis"]
        assert dx
        assert dx[0].severity is ConflictSeverity.HIGH
        assert ctx.conflict_report.requires_human_review is True

    def test_unlabeled_current_diagnosis_conflicts_with_labeled_diagnosis(self):
        psa = _doc(
            "psa.txt",
            DocumentCategory.CLINICAL_NOTE,
            (
                "Patient: Alan Grant. ID: CON006. Clinical summary: Patient "
                "being treated for severe Psoriatic Arthritis. MTX failed. "
                "TB negative. Rheum consult complete."
            ),
        )
        ra = _doc(
            "ra.txt",
            DocumentCategory.PRIOR_AUTH_FORM,
            "Patient: Alan Grant. ID: CON006. Diagnosis: Rheumatoid Arthritis. Requested: Humira.",
        )

        ctx = CaseAssemblyEngine().assemble("C1", [psa, ra])

        dx = [c for c in ctx.conflict_report.conflicts if c.fact_type == "diagnosis"]
        assert dx
        assert dx[0].severity is ConflictSeverity.HIGH
        assert ctx.conflict_report.requires_human_review is True
        assert ctx.patient_case.member_id == "CON006"
        assert [
            c for c in ctx.conflict_report.conflicts if c.fact_type == "patient_name"
        ] == []
        assert [
            c for c in ctx.conflict_report.conflicts if c.fact_type == "member_id"
        ] == []
        joined = " ".join(dx[0].values).lower()
        assert "psoriatic arthritis" in joined
        assert "rheumatoid arthritis" in joined

    def test_contextual_history_diagnosis_does_not_conflict(self):
        ra = _doc(
            "ra.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Diagnosis: Rheumatoid Arthritis",
        )
        history = _doc(
            "history.txt",
            DocumentCategory.CLINICAL_NOTE,
            "History of Psoriatic Arthritis",
        )

        ctx = CaseAssemblyEngine().assemble("C1", [ra, history])

        assert ctx.patient_case.diagnosis == "Rheumatoid Arthritis"
        assert [
            c for c in ctx.conflict_report.conflicts if c.fact_type == "diagnosis"
        ] == []
        assert ctx.conflict_report.requires_human_review is False

    def test_rule_out_and_differential_diagnoses_do_not_conflict(self):
        ra = _doc(
            "ra.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Diagnosis: Rheumatoid Arthritis",
        )
        rule_out = _doc(
            "rule-out.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Diagnosis: rule out Psoriatic Arthritis",
        )
        differential = _doc(
            "differential.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Differential diagnosis: Psoriatic Arthritis",
        )

        ctx = CaseAssemblyEngine().assemble("C1", [ra, rule_out, differential])

        assert ctx.patient_case.diagnosis == "Rheumatoid Arthritis"
        assert [
            c for c in ctx.conflict_report.conflicts if c.fact_type == "diagnosis"
        ] == []

    def test_tb_negative_and_positive_results_conflict(self):
        negative = _doc(
            "tb-negative.txt",
            DocumentCategory.LAB_RESULT,
            "TB negative",
        )
        positive = _doc(
            "quantiferon-positive.txt",
            DocumentCategory.LAB_RESULT,
            "Quantiferon-TB Gold POSITIVE",
        )

        ctx = CaseAssemblyEngine().assemble("C1", [negative, positive])

        tb = [
            c for c in ctx.conflict_report.conflicts
            if c.fact_type == "tb_screen_result"
        ]
        assert tb
        assert tb[0].severity is ConflictSeverity.HIGH
        assert ctx.conflict_report.requires_human_review is True
        tb_facts = {
            e.normalized_fact
            for e in ctx.evidence
            if e.fact_type == "tb_screen_result"
        }
        assert tb_facts == {
            "tb_screen_result: negative",
            "tb_screen_result: positive",
        }

    def test_unresolved_high_conflict_reduces_complete_case_confidence(self):
        complete = _doc(
            "complete.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Patient Name: Harold Greene\n"
            "Member ID: WP-1\n"
            "Date of Birth: 1970-01-01\n"
            "Diagnosis: Rheumatoid Arthritis\n"
            "ICD-10: M06.9\n"
            "Procedure: Humira\n"
            "CPT: 12345\n"
            "Insurance Company: Payer\n"
            "Status: DENIED\n"
            "Physician: Dr. Patel",
        )
        conflict = _doc(
            "conflict.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Diagnosis: Psoriatic Arthritis",
        )

        baseline = CaseAssemblyEngine().assemble("C1", [complete])
        conflicted = CaseAssemblyEngine().assemble("C1", [complete, conflict])

        assert baseline.patient_case.confidence_score == 1.0
        assert conflicted.patient_case.confidence_score == 0.65

    def test_provider_role_normalization_preserves_provenance(self):
        rheum = _doc(
            "rheum.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Rheum consult completed",
        )
        chiro = _doc(
            "chiro.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Seen by Chiropractic Care",
        )

        ctx = CaseAssemblyEngine().assemble("C1", [rheum, chiro])

        specialist = [
            e for e in ctx.evidence if e.fact_type == "criterion_specialist"
        ]
        assert len(specialist) == 1
        assert specialist[0].normalized_fact == (
            "criterion_specialist: rheumatology specialist"
        )
        assert specialist[0].source_filename == "rheum.txt"

    def test_conflicting_provider_roles_require_human_review(self):
        rheum = _doc(
            "rheum.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Rheumatologist recommends Humira.",
        )
        chiro = _doc(
            "chiro.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Chiropractor recommends Humira.",
        )

        ctx = CaseAssemblyEngine().assemble("C1", [rheum, chiro])

        provider = [
            c for c in ctx.conflict_report.conflicts
            if c.fact_type == "provider_role"
        ]
        assert provider
        assert provider[0].severity is ConflictSeverity.MEDIUM
        assert ctx.conflict_report.requires_human_review is True
        joined = " ".join(provider[0].values).lower()
        assert "rheumatology specialist" in joined
        assert "chiropractic provider" in joined

    def test_step_therapy_failed_and_refused_conflict(self):
        failed = _doc(
            "failed.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Methotrexate failed after 12 weeks.",
        )
        refused = _doc(
            "refused.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Patient refused methotrexate.",
        )

        ctx = CaseAssemblyEngine().assemble("C1", [failed, refused])

        step = [
            c for c in ctx.conflict_report.conflicts
            if c.fact_type == "step_therapy_status"
        ]
        assert step
        assert step[0].severity is ConflictSeverity.HIGH
        assert ctx.conflict_report.requires_human_review is True
        statuses = {
            e.normalized_fact
            for e in ctx.evidence
            if e.fact_type == "step_therapy_status"
        }
        assert statuses == {
            "step_therapy_status: failed",
            "step_therapy_status: refused",
        }

    def test_dermatology_role_normalizes_as_specialist(self):
        derm = _doc(
            "derm.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Derm follow-up completed",
        )

        ctx = CaseAssemblyEngine().assemble("C1", [derm])

        specialist = [
            e for e in ctx.evidence if e.fact_type == "criterion_specialist"
        ]
        assert specialist
        assert specialist[0].normalized_fact == (
            "criterion_specialist: dermatology specialist"
        )


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


class TestHumiraWorkflowNonRegression:
    def test_humira_approval_workflow_still_approves(self):
        docs = [
            _doc(
                "demographics.txt",
                DocumentCategory.CLINICAL_NOTE,
                "Patient Name: John Smith\n"
                "Member ID: JS-123\n"
                "Diagnosis: Rheumatoid Arthritis\n",
            ),
            _doc(
                "therapy.txt",
                DocumentCategory.CLINICAL_NOTE,
                "Methotrexate failed after 12 months\n",
            ),
            _doc(
                "pa.txt",
                DocumentCategory.PRIOR_AUTH_FORM,
                "TB screen negative\n"
                "Rheumatologist recommendation\n"
                "Requested Medication: Humira\n"
                "Status: DENIED\n",
            ),
        ]

        ctx = CaseAssemblyEngine().assemble("C1", docs)
        review = ClinicalReviewEngine().review(
            ctx.patient_case,
            "\n".join(d.raw_text for d in docs),
        )

        assert review.recommendation is Recommendation.APPROVE
        assert review.missing_criteria == []

    def test_humira_denial_workflow_still_denies_when_criteria_missing(self):
        ctx = CaseAssemblyEngine().assemble("C1", [DENIAL])
        review = ClinicalReviewEngine().review(ctx.patient_case, DENIAL.raw_text)

        assert review.recommendation is Recommendation.DENY
        assert review.missing_criteria


class TestGovernedRequestedServiceHealing:
    def test_synthesize_from_evidence_does_not_heal_requested_service_in_governed_mode(self):
        doc = _doc(
            "note.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Diagnosis: Rheumatoid Arthritis\nHumira appears in a payer comment.",
        )
        evidence = [
            EvidenceReference(
                case_id="C1",
                source_document_id=doc.document_id,
                source_filename=doc.filename,
                page_number=1,
                section_label="Diagnosis",
                quoted_text="Diagnosis: Rheumatoid Arthritis. Humira appears in a payer comment.",
                normalized_fact="diagnosis: Rheumatoid Arthritis",
                fact_type="diagnosis",
                confidence_score=0.9,
            )
        ]

        ctx = CaseAssemblyEngine().synthesize_from_evidence("C1", evidence, [doc])

        assert not [
            ev for ev in ctx.evidence if ev.fact_type == "requested_service"
        ]
        assert ctx.patient_case.requested_service is None

    def test_assemble_can_still_heal_requested_service_from_document_text(self):
        doc = _doc(
            "note.txt",
            DocumentCategory.CLINICAL_NOTE,
            "Diagnosis: Rheumatoid Arthritis\nHumira requested.",
        )

        ctx = CaseAssemblyEngine().assemble("C1", [doc])

        assert any(ev.fact_type == "requested_service" for ev in ctx.evidence)
        assert ctx.patient_case.requested_service == "Humira (adalimumab)"
