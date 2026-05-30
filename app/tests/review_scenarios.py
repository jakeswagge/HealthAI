"""Labeled clinical-review scenarios for evaluation.

Provides >= 10 approval, >= 10 denial, and >= 5 insufficient-information
scenarios as :class:`PatientCase` objects plus optional supporting document
text and the expected recommendation / guideline.

These are deterministic and offline. The supporting evidence (document_text) is
written so the rule-based :class:`ClinicalReviewEngine` reaches the labeled
recommendation; denial scenarios encode the deficiency in ``denial_reason``.
"""

from __future__ import annotations

from app.models.patient_case import Decision, PatientCase


def _case(**kwargs) -> PatientCase:
    return PatientCase(**kwargs)


# --------------------------------------------------------------------------- #
# APPROVALS (10) - all required criteria supported, no deficiency
# --------------------------------------------------------------------------- #
APPROVAL_SCENARIOS = [
    {
        "name": "humira_ra_full_criteria",
        "expected": "APPROVE",
        "expected_guideline": "GL-HUMIRA-001",
        "case": _case(
            patient_name="A One",
            diagnosis="Moderate to severe rheumatoid arthritis",
            icd10_codes=["M06.9"],
            requested_service="Humira (adalimumab)",
            cpt_codes=["J0135"],
            decision=Decision.APPROVED,
        ),
        "document_text": (
            "Moderate to severe rheumatoid arthritis confirmed. Patient failed "
            "methotrexate, a conventional DMARD, over 3 months. Tuberculosis "
            "screening negative (QuantiFERON). Prescribed by a rheumatologist."
        ),
    },
    {
        "name": "humira_crohn_full_criteria",
        "expected": "APPROVE",
        "expected_guideline": "GL-HUMIRA-001",
        "case": _case(
            patient_name="A Two",
            diagnosis="Crohn's disease, moderate",
            icd10_codes=["K50.90"],
            requested_service="adalimumab",
            decision=Decision.APPROVED,
        ),
        "document_text": (
            "Crohn disease confirmed. Failed conventional therapy with a DMARD "
            "immunomodulator. Negative TB test documented. Managed by a "
            "gastroenterologist."
        ),
    },
    {
        "name": "enbrel_ra_full_criteria",
        "expected": "APPROVE",
        "expected_guideline": "GL-ENBREL-001",
        "case": _case(
            patient_name="A Three",
            diagnosis="Moderate to severe rheumatoid arthritis",
            icd10_codes=["M06.9"],
            requested_service="Enbrel (etanercept)",
            cpt_codes=["J1438"],
            decision=Decision.APPROVED,
        ),
        "document_text": (
            "Moderate to severe rheumatoid arthritis. Failed methotrexate "
            "(DMARD). TB screen negative. Rheumatologist prescribing."
        ),
    },
    {
        "name": "enbrel_psoriasis_full_criteria",
        "expected": "APPROVE",
        "expected_guideline": "GL-ENBREL-001",
        "case": _case(
            patient_name="A Four",
            diagnosis="Severe plaque psoriasis",
            icd10_codes=["L40.0"],
            requested_service="etanercept",
            decision=Decision.APPROVED,
        ),
        "document_text": (
            "Moderate to severe plaque psoriasis. Failed phototherapy and "
            "systemic therapy. Negative TB (QuantiFERON). Under care of a "
            "dermatologist."
        ),
    },
    {
        "name": "mri_lumbar_radiculopathy",
        "expected": "APPROVE",
        "expected_guideline": "GL-MRI-LUMBAR-001",
        "case": _case(
            patient_name="A Five",
            diagnosis="Lumbar radiculopathy",
            icd10_codes=["M54.16"],
            requested_service="MRI Lumbar Spine",
            cpt_codes=["72148"],
            decision=Decision.APPROVED,
        ),
        "document_text": (
            "Completed 6 weeks of physical therapy and NSAIDs without relief. "
            "Exam shows radiculopathy with leg weakness."
        ),
    },
    {
        "name": "mri_lumbar_radicular_numbness",
        "expected": "APPROVE",
        "expected_guideline": "GL-MRI-LUMBAR-001",
        "case": _case(
            patient_name="A Six",
            diagnosis="Low back pain with radicular symptoms",
            icd10_codes=["M51.16"],
            requested_service="Lumbar spine MRI",
            cpt_codes=["72149"],
            decision=Decision.APPROVED,
        ),
        "document_text": (
            "Completed six weeks of conservative therapy without improvement. "
            "Numbness in the left leg in a radicular pattern."
        ),
    },
    {
        "name": "ct_chest_nodule",
        "expected": "APPROVE",
        "expected_guideline": "GL-CT-CHEST-001",
        "case": _case(
            patient_name="A Seven",
            diagnosis="Pulmonary nodule",
            icd10_codes=["R91.1"],
            requested_service="CT Chest",
            cpt_codes=["71250"],
            decision=Decision.APPROVED,
        ),
        "document_text": (
            "Pulmonary nodule identified on a prior chest x-ray. Follow-up CT "
            "recommended for characterization."
        ),
    },
    {
        "name": "ct_chest_suspected_malignancy",
        "expected": "APPROVE",
        "expected_guideline": "GL-CT-CHEST-001",
        "case": _case(
            patient_name="A Eight",
            diagnosis="Suspected thoracic malignancy",
            icd10_codes=["C34.90"],
            requested_service="Chest CT",
            cpt_codes=["71260"],
            decision=Decision.APPROVED,
        ),
        "document_text": (
            "Abnormal chest x-ray showing a mass; suspected malignancy. Prior "
            "imaging performed."
        ),
    },
    {
        "name": "pt_knee_full_criteria",
        "expected": "APPROVE",
        "expected_guideline": "GL-PT-001",
        "case": _case(
            patient_name="A Nine",
            diagnosis="Knee pain after injury",
            icd10_codes=["M25.561"],
            requested_service="Physical Therapy",
            cpt_codes=["97110"],
            decision=Decision.APPROVED,
        ),
        "document_text": (
            "Functional deficit with reduced range of motion. Plan of care with "
            "measurable goals, frequency 2x/week for 6 weeks. Physician "
            "referral on file."
        ),
    },
    {
        "name": "pt_shoulder_full_criteria",
        "expected": "APPROVE",
        "expected_guideline": "GL-PT-001",
        "case": _case(
            patient_name="A Ten",
            diagnosis="Rotator cuff strain",
            icd10_codes=["M75.100"],
            requested_service="Physiotherapy",
            cpt_codes=["97112"],
            decision=Decision.APPROVED,
        ),
        "document_text": (
            "Documented impairment and limited function. Treatment plan with "
            "goals and duration defined. Ordered by the physician."
        ),
    },
]


# --------------------------------------------------------------------------- #
# DENIALS (10) - denial_reason encodes an unmet required criterion
# --------------------------------------------------------------------------- #
DENIAL_SCENARIOS = [
    {
        "name": "humira_missing_step_therapy",  # success-criterion scenario
        "expected": "DENY",
        "expected_guideline": "GL-HUMIRA-001",
        "case": _case(
            patient_name="D One",
            diagnosis="Rheumatoid arthritis",
            icd10_codes=["M06.9"],
            requested_service="Humira (adalimumab)",
            cpt_codes=["J0135"],
            decision=Decision.DENIED,
            denial_reason=(
                "Request denied: step therapy requirement not met. No "
                "documented trial and failure of methotrexate or another "
                "conventional DMARD."
            ),
        ),
    },
    {
        "name": "humira_missing_tb_screen",
        "expected": "DENY",
        "expected_guideline": "GL-HUMIRA-001",
        "case": _case(
            patient_name="D Two",
            diagnosis="Rheumatoid arthritis",
            icd10_codes=["M06.9"],
            requested_service="Humira (adalimumab)",
            decision=Decision.DENIED,
            denial_reason=(
                "Denied. No documentation of negative tuberculosis (TB) "
                "screening prior to initiation of therapy."
            ),
        ),
    },
    {
        "name": "enbrel_missing_step_therapy",
        "expected": "DENY",
        "expected_guideline": "GL-ENBREL-001",
        "case": _case(
            patient_name="D Three",
            diagnosis="Rheumatoid arthritis",
            icd10_codes=["M06.9"],
            requested_service="Enbrel (etanercept)",
            decision=Decision.DENIED,
            denial_reason=(
                "Denied due to missing step therapy: no trial of methotrexate "
                "(conventional DMARD) documented."
            ),
        ),
    },
    {
        "name": "enbrel_missing_specialist",
        "expected": "DENY",
        "expected_guideline": "GL-ENBREL-001",
        "case": _case(
            patient_name="D Four",
            diagnosis="Plaque psoriasis",
            icd10_codes=["L40.0"],
            requested_service="etanercept",
            decision=Decision.DENIED,
            denial_reason=(
                "Denied: prescription was not written by or in consultation "
                "with a rheumatologist or dermatologist (specialist "
                "requirement unmet)."
            ),
        ),
    },
    {
        "name": "mri_lumbar_missing_conservative",
        "expected": "DENY",
        "expected_guideline": "GL-MRI-LUMBAR-001",
        "case": _case(
            patient_name="D Five",
            diagnosis="Low back pain",
            icd10_codes=["M54.50"],
            requested_service="MRI Lumbar Spine",
            cpt_codes=["72148"],
            decision=Decision.DENIED,
            denial_reason=(
                "Denied as not medically necessary: no documentation of 6 "
                "weeks of conservative therapy (physical therapy or NSAIDs)."
            ),
        ),
    },
    {
        "name": "mri_lumbar_missing_neuro",
        "expected": "DENY",
        "expected_guideline": "GL-MRI-LUMBAR-001",
        "case": _case(
            patient_name="D Six",
            diagnosis="Low back pain",
            icd10_codes=["M54.50"],
            requested_service="Lumbar spine MRI",
            decision=Decision.DENIED,
            denial_reason=(
                "Denied: no documented neurological findings or radiculopathy "
                "to justify advanced imaging."
            ),
        ),
    },
    {
        "name": "ct_chest_missing_prior_imaging",
        "expected": "DENY",
        "expected_guideline": "GL-CT-CHEST-001",
        "case": _case(
            patient_name="D Seven",
            diagnosis="Cough",
            icd10_codes=["R05.9"],
            requested_service="CT Chest",
            cpt_codes=["71250"],
            decision=Decision.DENIED,
            denial_reason=(
                "Denied: no prior chest x-ray or chest radiograph performed "
                "before requesting advanced imaging."
            ),
        ),
    },
    {
        "name": "ct_chest_missing_indication",
        "expected": "DENY",
        "expected_guideline": "GL-CT-CHEST-001",
        "case": _case(
            patient_name="D Eight",
            diagnosis="Chest discomfort",
            icd10_codes=["R07.9"],
            requested_service="Chest CT",
            decision=Decision.DENIED,
            denial_reason=(
                "Denied: no clear clinical indication; no pulmonary nodule or "
                "suspected malignancy documented."
            ),
        ),
    },
    {
        "name": "pt_missing_treatment_plan",
        "expected": "DENY",
        "expected_guideline": "GL-PT-001",
        "case": _case(
            patient_name="D Nine",
            diagnosis="Back pain",
            icd10_codes=["M54.9"],
            requested_service="Physical Therapy",
            cpt_codes=["97110"],
            decision=Decision.DENIED,
            denial_reason=(
                "Denied: no defined plan of care with measurable goals was "
                "submitted with the request."
            ),
        ),
    },
    {
        "name": "pt_missing_referral",
        "expected": "DENY",
        "expected_guideline": "GL-PT-001",
        "case": _case(
            patient_name="D Ten",
            diagnosis="Ankle sprain",
            icd10_codes=["S93.409A"],
            requested_service="Physical Therapy",
            decision=Decision.DENIED,
            denial_reason=(
                "Denied: no physician referral or order for physical therapy "
                "was provided."
            ),
        ),
    },
]


# --------------------------------------------------------------------------- #
# INSUFFICIENT INFORMATION (5) - partial evidence, no explicit deficiency
# --------------------------------------------------------------------------- #
INSUFFICIENT_SCENARIOS = [
    {
        "name": "humira_only_diagnosis",
        "expected": "INSUFFICIENT_INFORMATION",
        "expected_guideline": "GL-HUMIRA-001",
        "case": _case(
            patient_name="I One",
            diagnosis="Rheumatoid arthritis",
            icd10_codes=["M06.9"],
            requested_service="Humira (adalimumab)",
            decision=Decision.UNKNOWN,
        ),
        "document_text": "Patient has rheumatoid arthritis.",
    },
    {
        "name": "mri_lumbar_only_neuro",
        "expected": "INSUFFICIENT_INFORMATION",
        "expected_guideline": "GL-MRI-LUMBAR-001",
        "case": _case(
            patient_name="I Two",
            diagnosis="Lumbar radiculopathy",
            icd10_codes=["M54.16"],
            requested_service="MRI Lumbar Spine",
            decision=Decision.UNKNOWN,
        ),
        "document_text": "Exam shows radiculopathy.",
    },
    {
        "name": "ct_chest_only_indication",
        "expected": "INSUFFICIENT_INFORMATION",
        "expected_guideline": "GL-CT-CHEST-001",
        "case": _case(
            patient_name="I Three",
            diagnosis="Pulmonary nodule",
            icd10_codes=["R91.1"],
            requested_service="CT Chest",
            decision=Decision.UNKNOWN,
        ),
        "document_text": "Pulmonary nodule noted on examination.",
    },
    {
        "name": "pt_only_functional_deficit",
        "expected": "INSUFFICIENT_INFORMATION",
        "expected_guideline": "GL-PT-001",
        "case": _case(
            patient_name="I Four",
            diagnosis="Shoulder pain",
            icd10_codes=["M25.519"],
            requested_service="Physical Therapy",
            decision=Decision.UNKNOWN,
        ),
        "document_text": "Patient reports pain and limited function.",
    },
    {
        "name": "enbrel_dx_and_specialist_only",
        "expected": "INSUFFICIENT_INFORMATION",
        "expected_guideline": "GL-ENBREL-001",
        "case": _case(
            patient_name="I Five",
            diagnosis="Plaque psoriasis",
            icd10_codes=["L40.0"],
            requested_service="Enbrel (etanercept)",
            decision=Decision.UNKNOWN,
        ),
        "document_text": "Plaque psoriasis managed by a dermatologist.",
    },
]


ALL_SCENARIOS = APPROVAL_SCENARIOS + DENIAL_SCENARIOS + INSUFFICIENT_SCENARIOS
