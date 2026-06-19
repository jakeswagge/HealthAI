"""Tests for validation-facing importer entrypoints."""

from __future__ import annotations

import json

from app.validation.import_entrypoint import (
    discover_classifymymeds_files,
    run_classifymymeds_benchmark,
    sync_davinci_formulary,
)
from validation.run import main


def _write_classifymymeds_sample(tmp_path):
    dim_pa = tmp_path / "data" / "dim_pa.csv"
    dim_pa.parent.mkdir()
    dim_pa.write_text(
        "dim_pa_id,correct_diagnosis,tried_and_failed,contraindication,pa_approved\n"
        "1,1,1,0,1\n"
        "2,1,0,0,0\n"
        "3,1,1,1,0\n",
        encoding="utf-8",
    )
    dim_claims = tmp_path / "data" / "dim_claims.csv"
    dim_claims.write_text(
        "dim_claim_id,bin,drug,reject_code,pharmacy_claim_approved\n"
        "10,417380,Humira,75,0\n"
        "20,417614,Enbrel,70,0\n"
        "30,417614,Humira,70,0\n",
        encoding="utf-8",
    )
    bridge = tmp_path / "data" / "bridge.csv"
    bridge.write_text(
        "dim_claim_id,dim_pa_id,dim_date_id\n"
        "10,1,100\n"
        "20,2,200\n"
        "30,3,300\n",
        encoding="utf-8",
    )
    return dim_pa, dim_claims, bridge


def test_classifymymeds_validation_entrypoint_discovers_and_scores(tmp_path):
    _write_classifymymeds_sample(tmp_path)

    discovered = discover_classifymymeds_files(tmp_path)
    report = run_classifymymeds_benchmark(dataset_dir=tmp_path)

    assert discovered["dim_pa_csv"].name == "dim_pa.csv"
    assert discovered["dim_claims_csv"].name == "dim_claims.csv"
    assert discovered["bridge_csv"].name == "bridge.csv"
    assert report.total_cases == 3
    assert report.passed == 3
    assert report.failed == 0
    assert report.accuracy == 1.0
    assert report.results[1].failure_reasons == ["step_therapy_not_established"]
    assert report.results[2].failure_reasons == ["contraindication_present"]


def test_davinci_formulary_validation_entrypoint_writes_catalog(tmp_path):
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "MedicationKnowledge",
                    "id": "Drug-1",
                    "code": {"coding": [{"code": "123", "display": "Drug One"}]},
                }
            },
            {
                "resource": {
                    "resourceType": "Basic",
                    "id": "Item-1",
                    "code": {"coding": [{"code": "formulary-item"}]},
                    "subject": {"reference": "MedicationKnowledge/Drug-1"},
                    "extension": [
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-PriorAuthorization-extension",
                            "valueBoolean": True,
                        },
                        {
                            "url": "http://hl7.org/fhir/us/davinci-drug-formulary/StructureDefinition/usdf-StepTherapyLimit-extension",
                            "valueBoolean": True,
                        },
                    ],
                }
            },
        ],
    }
    source = tmp_path / "bundle.json"
    output = tmp_path / "catalog.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    catalog, report = sync_davinci_formulary(source=source, output_path=output)

    assert output.exists()
    assert report.source_type == "bundle"
    assert report.drugs == 1
    assert report.items == 1
    assert report.prior_authorization_required == 1
    assert report.step_therapy_required == 1
    assert len(catalog.items) == 1
    assert json.loads(output.read_text(encoding="utf-8"))["items"][0]["item_id"] == "Item-1"


def test_validation_cli_benchmark_and_formulary_commands(tmp_path, capsys):
    dim_pa, dim_claims, bridge = _write_classifymymeds_sample(tmp_path)

    benchmark_exit = main(
        [
            "benchmark",
            "--dim-pa-csv",
            str(dim_pa),
            "--dim-claims-csv",
            str(dim_claims),
            "--bridge-csv",
            str(bridge),
        ]
    )
    benchmark_out = capsys.readouterr().out

    assert benchmark_exit == 0
    assert "ClassifyMyMeds benchmark: 3/3 cases passed" in benchmark_out

    bundle_path = tmp_path / "empty-bundle.json"
    output_path = tmp_path / "formulary-output.json"
    bundle_path.write_text('{"resourceType": "Bundle", "entry": []}', encoding="utf-8")

    formulary_exit = main(
        [
            "formulary",
            "--source",
            str(bundle_path),
            "--output",
            str(output_path),
        ]
    )
    formulary_out = capsys.readouterr().out

    assert formulary_exit == 0
    assert output_path.exists()
    assert "Da Vinci formulary sync: 0 plan(s), 0 drug(s), 0 formulary item(s)." in formulary_out


def test_validation_cli_benchmark_missing_dataset_is_clean_error(capsys):
    exit_code = main(
        [
            "benchmark",
            "--dataset-dir",
            "path\\to\\classifymymeds",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "ClassifyMyMeds dataset directory not found" in captured.err
    assert "Hint: clone the dataset first" in captured.err


def test_validation_cli_benchmark_mismatches_do_not_fail_by_default(tmp_path, capsys):
    dim_pa = tmp_path / "dim_pa.csv"
    dim_pa.write_text(
        "dim_pa_id,correct_diagnosis,tried_and_failed,contraindication,pa_approved\n"
        "1,1,0,0,1\n",
        encoding="utf-8",
    )

    default_exit = main(["benchmark", "--dim-pa-csv", str(dim_pa)])
    default_out = capsys.readouterr().out
    strict_exit = main(
        ["benchmark", "--dim-pa-csv", str(dim_pa), "--fail-on-mismatch"]
    )

    assert default_exit == 0
    assert "mismatches are reported" in default_out
    assert strict_exit == 1
