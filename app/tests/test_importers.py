"""Tests for external benchmark and FHIR formulary importers."""

from __future__ import annotations

import json

from app.importers.classifymymeds import ClassifyMyMedsBenchmarkImporter
from app.importers.davinci_formulary import DaVinciFormularyAdapter
from app.models.review_result import Recommendation


def test_classifymymeds_importer_loads_joined_cases(tmp_path):
    dim_pa = tmp_path / "dim_pa.csv"
    dim_pa.write_text(
        "dim_pa_id,correct_diagnosis,tried_and_failed,contraindication,pa_approved\n"
        "1,1,1,0,1\n"
        "2,1,0,0,0\n",
        encoding="utf-8",
    )
    dim_claims = tmp_path / "dim_claims.csv"
    dim_claims.write_text(
        "dim_claim_id,bin,drug,reject_code,pharmacy_claim_approved\n"
        "10,417380,A,75,0\n"
        "20,417614,B,70,0\n",
        encoding="utf-8",
    )
    dim_date = tmp_path / "dim_date.csv"
    dim_date.write_text(
        "dim_date_id,date_val,calendar_year,calendar_month,calendar_day\n"
        "100,2018-01-15,2018,1,15\n"
        "200,2018-01-16,2018,1,16\n",
        encoding="utf-8",
    )
    bridge = tmp_path / "bridge.csv"
    bridge.write_text(
        "dim_claim_id,dim_pa_id,dim_date_id\n"
        "10,1,100\n"
        "20,2,200\n",
        encoding="utf-8",
    )

    cases = ClassifyMyMedsBenchmarkImporter().load_cases(
        dim_pa_csv=dim_pa,
        bridge_csv=bridge,
        dim_claims_csv=dim_claims,
        dim_date_csv=dim_date,
    )
    summary = ClassifyMyMedsBenchmarkImporter.summarize(cases)

    assert len(cases) == 2
    assert cases[0].case_id == "CMM-PA-1"
    assert cases[0].payer_bin == "417380"
    assert cases[0].drug == "A"
    assert cases[0].claim_date == "2018-01-15"
    assert cases[0].criteria_labels == {
        "correct_diagnosis": True,
        "tried_and_failed": True,
        "contraindication": False,
    }
    assert cases[0].expected_recommendation is Recommendation.APPROVE
    assert cases[1].expected_recommendation is Recommendation.DENY
    assert summary.total_cases == 2
    assert summary.approved_cases == 1
    assert summary.by_drug == {"A": 1, "B": 1}


def test_classifymymeds_importer_can_limit_pa_only_load(tmp_path):
    dim_pa = tmp_path / "dim_pa.csv"
    dim_pa.write_text(
        "dim_pa_id,correct_diagnosis,tried_and_failed,contraindication,pa_approved\n"
        "1,1,1,0,1\n"
        "2,0,1,0,0\n",
        encoding="utf-8",
    )

    cases = ClassifyMyMedsBenchmarkImporter().load_cases(
        dim_pa_csv=dim_pa,
        limit=1,
    )

    assert len(cases) == 1
    assert cases[0].source_ids == {"dim_pa_id": "1"}


def test_davinci_formulary_adapter_parses_bundle_resources():
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "InsurancePlan",
                    "id": "FormularyD1002",
                    "name": "Sample Medicare Advantage Part D Formulary D1002",
                    "status": "active",
                    "type": [{"coding": [{"code": "DRUGPOL"}]}],
                }
            },
            {
                "resource": {
                    "resourceType": "MedicationKnowledge",
                    "id": "FormularyDrug-1000091",
                    "status": "active",
                    "code": {
                        "coding": [
                            {
                                "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                                "code": "1000091",
                                "display": "doxepin hydrochloride 50 MG/ML Topical Cream",
                            }
                        ]
                    },
                }
            },
            {
                "resource": {
                    "resourceType": "Basic",
                    "id": "FormularyItem-D1002-1000091",
                    "extension": [
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-FormularyReference-extension",
                            "valueReference": {"reference": "InsurancePlan/FormularyD1002"},
                        },
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-AvailabilityStatus-extension",
                            "valueCode": "active",
                        },
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-PharmacyBenefitType-extension",
                            "valueCodeableConcept": {
                                "coding": [{"code": "1-month-in-retail"}]
                            },
                        },
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-DrugTierID-extension",
                            "valueCodeableConcept": {
                                "coding": [{"code": "generic"}]
                            },
                        },
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-AvailabilityPeriod-extension",
                            "valuePeriod": {"start": "2021-01-01", "end": "2021-12-31"},
                        },
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-PriorAuthorization-extension",
                            "valueBoolean": False,
                        },
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-StepTherapyLimit-extension",
                            "valueBoolean": True,
                        },
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-StepTherapyLimitNewStartsOnly-extension",
                            "valueBoolean": True,
                        },
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-QuantityLimit-extension",
                            "valueBoolean": True,
                        },
                    ],
                    "code": {"coding": [{"code": "formulary-item"}]},
                    "subject": {"reference": "MedicationKnowledge/FormularyDrug-1000091"},
                }
            },
        ],
    }

    catalog = DaVinciFormularyAdapter().from_bundle(bundle)

    assert len(catalog.plans) == 1
    assert catalog.plans[0].plan_id == "FormularyD1002"
    assert catalog.plans[0].plan_type_codes == ["DRUGPOL"]
    assert len(catalog.drugs) == 1
    assert catalog.drugs[0].code == "1000091"
    assert catalog.drugs[0].display == "doxepin hydrochloride 50 MG/ML Topical Cream"
    assert len(catalog.items) == 1
    item = catalog.items[0]
    assert item.formulary_id == "FormularyD1002"
    assert item.drug_reference == "FormularyDrug-1000091"
    assert item.pharmacy_benefit_types == ["1-month-in-retail"]
    assert item.drug_tier == "generic"
    assert item.availability_start == "2021-01-01"
    assert item.availability_end == "2021-12-31"
    assert item.prior_authorization_required is False
    assert item.step_therapy_required is True
    assert item.step_therapy_new_starts_only is True
    assert item.quantity_limit is True
    assert catalog.item_for_drug("MedicationKnowledge/FormularyDrug-1000091") == [item]


def test_davinci_formulary_adapter_loads_directory(tmp_path):
    (tmp_path / "export.json").write_text(
        json.dumps(
            {
                "transactionTime": "2022-01-01T00:00:00Z",
                "request": "MedicationKnowledge",
                "output": [{"type": "MedicationKnowledge", "url": "MedicationKnowledge.ndjson"}],
            }
        ),
        encoding="utf-8",
    )
    item = {
        "resourceType": "Basic",
        "id": "FormularyItem-D3001-ABC",
        "code": {"coding": [{"code": "formulary-item"}]},
        "subject": {"reference": "MedicationKnowledge/ABC"},
        "extension": [
            {
                "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-PriorAuthorization-extension",
                "valueBoolean": True,
            }
        ],
    }
    (tmp_path / "item.json").write_text(json.dumps(item), encoding="utf-8")

    catalog = DaVinciFormularyAdapter().from_directory(tmp_path)

    assert len(catalog.items) == 1
    assert catalog.items[0].prior_authorization_required is True
