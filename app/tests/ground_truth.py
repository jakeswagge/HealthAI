"""Ground-truth expectations for the sample document corpus.

Used by the evaluation framework to measure field-extraction accuracy. Only
fields with a confident, unambiguous expected value are listed per document;
code lists are checked as subsets so a backend may find additional valid codes
without being penalized.

Values mirror what is literally written in ``data/sample_docs``.
"""

from __future__ import annotations

# Each entry maps a sample filename to expected field values.
# Keys may include: patient_name, member_id, date_of_birth, decision,
# insurance_company, requested_service_contains, icd10_includes, cpt_includes,
# physician_contains, denial_reason_required (bool).
GROUND_TRUTH: dict[str, dict] = {
    # ------------------------- APPROVALS ------------------------- #
    "approval_case_01.txt": {
        "patient_name": "Maria S. Delgado",
        "member_id": "MHP-3320118",
        "date_of_birth": "11/04/1979",
        "decision": "approved",
        "insurance_company_contains": "Meridian",
        "icd10_includes": ["M23.205"],
        "cpt_includes": ["73721"],
        "physician_contains": "Nguyen",
        "denial_reason_expected_none": True,
    },
    "approval_case_02.txt": {
        "patient_name": "Robert Chen",
        "member_id": "AET7741203",
        "date_of_birth": "06/15/1990",
        "decision": "approved",
        "insurance_company_contains": "Aetna",
        "icd10_includes": ["G47.33"],
        "cpt_includes": ["95806"],
        "physician_contains": "Park",
        "denial_reason_expected_none": True,
    },
    "approval_case_03.txt": {
        "patient_name": "Sandra O. Whitaker",
        "member_id": "UHC-902118746",
        "date_of_birth": "12/30/1955",
        "decision": "approved",
        "insurance_company_contains": "UnitedHealthcare",
        "icd10_includes": ["H25.011"],
        "cpt_includes": ["66984"],
        "physician_contains": "Webb",
        "denial_reason_expected_none": True,
    },
    "approval_case_04.txt": {
        "patient_name": "Thomas R. Delacroix",
        "member_id": "CIG-3380012",
        "date_of_birth": "03/07/1972",
        "decision": "approved",
        "insurance_company_contains": "Cigna",
        "icd10_includes": ["K21.9"],
        "cpt_includes": ["43235"],
        "physician_contains": "Foster",
        "denial_reason_expected_none": True,
    },
    "approval_case_05.txt": {
        "patient_name": "Emily J. Navarro",
        "member_id": "BCBS-44820019",
        "date_of_birth": "09/19/1988",
        "decision": "approved",
        "insurance_company_contains": "Blue Cross",
        "icd10_includes": ["M51.16"],
        "cpt_includes": ["62323"],
        "physician_contains": "Lawson",
        "denial_reason_expected_none": True,
    },
    # -------------------------- DENIALS -------------------------- #
    "denial_case_01.txt": {
        "patient_name": "Johnathan A. Reyes",
        "member_id": "MHP-4471900",
        "date_of_birth": "08/22/1968",
        "decision": "denied",
        "insurance_company_contains": "Meridian",
        "icd10_includes": ["I42.0"],
        "cpt_includes": ["75561"],
        "physician_contains": "Whitfield",
        "denial_reason_required": True,
    },
    "denial_case_02.txt": {
        "patient_name": "Gregory P. Salinas",
        "member_id": "HUM-6610294",
        "date_of_birth": "07/11/1963",
        "decision": "denied",
        "insurance_company_contains": "Humana",
        "icd10_includes": ["M54.50"],
        "cpt_includes": ["72148"],
        "physician_contains": "Abbott",
        "denial_reason_required": True,
    },
    "denial_case_03.txt": {
        "patient_name": "Denise A. Holloway",
        "member_id": "KP-330018822",
        "date_of_birth": "02/02/1980",
        "decision": "denied",
        "insurance_company_contains": "Kaiser",
        "icd10_includes": ["E66.01"],
        "cpt_includes": ["43775"],
        "physician_contains": "Reyes",
        "denial_reason_required": True,
    },
    "denial_case_04.txt": {
        "patient_name": "Marcus J. Bellamy",
        "member_id": "ANT-5582019",
        "date_of_birth": "10/25/1995",
        "decision": "denied",
        "insurance_company_contains": "Anthem",
        "icd10_includes": ["M17.0"],
        "cpt_includes": ["20611"],
        "physician_contains": "Soto",
        "denial_reason_required": True,
    },
    "denial_case_05.txt": {
        # Intentionally missing CPT + physician to test graceful handling.
        "patient_name": "Yolanda Cruz",
        "member_id": "MOL-7781340",
        "date_of_birth": "11/28/1974",
        "decision": "denied",
        "insurance_company_contains": "Molina",
        "icd10_includes": ["G43.119"],
        "denial_reason_required": True,
        "expected_missing": ["cpt_codes", "physician_name"],
    },
}

APPROVAL_FILES = [f for f in GROUND_TRUTH if f.startswith("approval")]
DENIAL_FILES = [f for f in GROUND_TRUTH if f.startswith("denial")]
