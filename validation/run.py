"""CLI entry point for validation workflows.

Usage:
    python -m validation.run            # run bundled scenarios, print summary
    python -m validation.run --json     # emit the full report as JSON
    python -m validation.run benchmark --dataset-dir external/classifymymeds
    python -m validation.run formulary --source path/to/fhir/export

Exit code is 0 when all checks pass, 1 otherwise - so it can gate CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.models.governance import GovernanceSettings
from app.validation.import_entrypoint import (
    DEFAULT_FORMULARY_OUTPUT,
    run_classifymymeds_benchmark,
    sync_davinci_formulary,
)
from app.validation.clinical_accuracy import (
    AutoDecisionPolicy,
    ConfidenceCalibration,
    build_confidence_calibration,
    evaluate_clinical_gold_set,
)
from app.validation.gold_set import (
    DEFAULT_MATRIX_GOLD_SET,
    load_adjudicated_clinical_gold_set,
    load_matrix_clinical_gold_set,
    load_seed_clinical_gold_set,
)
from app.validation.runner import ValidationRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HealthAI validation runner")
    parser.add_argument(
        "--json", action="store_true", help="Emit the full report as JSON."
    )
    subparsers = parser.add_subparsers(dest="command")

    scenarios_parser = subparsers.add_parser(
        "scenarios",
        help="Run bundled validation scenarios.",
    )
    scenarios_parser.add_argument(
        "--json", action="store_true", help="Emit the full report as JSON."
    )

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Import and score the ClassifyMyMeds benchmark.",
    )
    benchmark_parser.add_argument(
        "--dataset-dir",
        help="Path to a cloned classifymymeds dataset; common CSV names are auto-detected.",
    )
    benchmark_parser.add_argument("--dim-pa-csv", help="Path to dim_pa.csv.")
    benchmark_parser.add_argument("--bridge-csv", help="Path to the bridge CSV.")
    benchmark_parser.add_argument("--dim-claims-csv", help="Path to dim_claims.csv.")
    benchmark_parser.add_argument("--dim-date-csv", help="Path to dim_date.csv.")
    benchmark_parser.add_argument(
        "--limit", type=int, help="Limit cases for a quick smoke run."
    )
    benchmark_parser.add_argument(
        "--output", help="Optional path to write the benchmark report JSON."
    )
    benchmark_parser.add_argument(
        "--json", action="store_true", help="Emit the full report as JSON."
    )
    benchmark_parser.add_argument(
        "--fail-on-mismatch",
        action="store_true",
        help="Return exit code 1 when benchmark scoring finds mismatches.",
    )

    formulary_parser = subparsers.add_parser(
        "formulary",
        help="Sync normalized Da Vinci formulary FHIR resources.",
    )
    formulary_parser.add_argument(
        "--source",
        required=True,
        help="FHIR Bundle JSON, NDJSON, directory, or base FHIR URL.",
    )
    formulary_parser.add_argument(
        "--source-type",
        default="auto",
        choices=["auto", "directory", "ndjson", "bundle", "url"],
        help="How to read --source. Defaults to auto-detection.",
    )
    formulary_parser.add_argument(
        "--resource-type",
        action="append",
        dest="resource_types",
        help="Resource type to fetch from a FHIR URL; may be repeated.",
    )
    formulary_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="FHIR URL fetch timeout.",
    )
    formulary_parser.add_argument(
        "--output",
        default=str(DEFAULT_FORMULARY_OUTPUT),
        help="Path to write the normalized catalog JSON.",
    )
    formulary_parser.add_argument(
        "--no-write",
        action="store_true",
        help="Parse and summarize without writing a normalized catalog.",
    )
    formulary_parser.add_argument(
        "--json", action="store_true", help="Emit the sync report as JSON."
    )

    clinical_parser = subparsers.add_parser(
        "clinical-accuracy",
        help="Evaluate clinical-review auto-decision accuracy against a gold set.",
    )
    clinical_parser.add_argument(
        "--gold-set",
        default=str(DEFAULT_MATRIX_GOLD_SET),
        help="Path to a matrix or adjudicated gold-set JSON.",
    )
    clinical_parser.add_argument(
        "--seed",
        action="store_true",
        help="Use built-in seed review scenarios instead of a matrix JSON file.",
    )
    clinical_parser.add_argument(
        "--adjudicated",
        action="store_true",
        help="Load an explicit reviewer-adjudicated gold set file.",
    )
    clinical_parser.add_argument(
        "--auto-threshold",
        type=float,
        default=0.99,
        help="Minimum confidence for auto-decided cases.",
    )
    clinical_parser.add_argument(
        "--target-accuracy",
        type=float,
        default=0.999,
        help="Release-gate target accuracy for auto-decided cases.",
    )
    clinical_parser.add_argument(
        "--allow-untraceable",
        action="store_true",
        help="Do not abstain solely because criterion evidence IDs are absent.",
    )
    clinical_parser.add_argument(
        "--calibrate-from-gold",
        action="store_true",
        help=(
            "Learn confidence caps from this gold set before scoring. Use only "
            "for training/smoke runs, not locked holdout reporting."
        ),
    )
    clinical_parser.add_argument(
        "--calibration-input",
        help="Path to a confidence calibration JSON artifact to apply.",
    )
    clinical_parser.add_argument(
        "--calibration-output",
        help="Optional path to write learned confidence calibration JSON.",
    )
    clinical_parser.add_argument(
        "--calibration-min-examples",
        type=int,
        default=5,
        help="Minimum examples required before a calibration bucket is trusted.",
    )
    clinical_parser.add_argument(
        "--output",
        help="Optional path to write the clinical accuracy report JSON.",
    )
    clinical_parser.add_argument(
        "--json", action="store_true", help="Emit the full report as JSON."
    )
    clinical_parser.add_argument(
        "--fail-on-gate",
        action="store_true",
        help="Return exit code 1 if the 99.9 release gate is not met.",
    )

    args = parser.parse_args(argv)

    try:
        if args.command == "benchmark":
            return _run_benchmark_command(args)
        if args.command == "formulary":
            return _run_formulary_command(args)
        if args.command == "clinical-accuracy":
            return _run_clinical_accuracy_command(args)

        json_output = bool(args.json)
        return _run_scenarios(json_output=json_output)
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.command == "benchmark":
            print(
                "Hint: clone the dataset first, then run: "
                ".venv\\Scripts\\python.exe -m validation.run benchmark "
                "--dataset-dir external\\classifymymeds "
                "--output validation\\classifymymeds_report.json",
                file=sys.stderr,
            )
        elif args.command == "formulary":
            print(
                "Hint: clone the Da Vinci repo first, then run: "
                ".venv\\Scripts\\python.exe -m validation.run formulary "
                "--source external\\drug-formulary-ri\\src\\main\\webapp\\resources "
                "--output validation\\davinci_formulary_catalog.json",
                file=sys.stderr,
            )
        return 2


def _run_scenarios(*, json_output: bool = False) -> int:
    runner = ValidationRunner(settings=GovernanceSettings())
    report = runner.run()

    if json_output:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(
            f"Validation: {report.passed}/{report.total} checks passed "
            f"({report.pass_rate:.0%})."
        )
        for r in report.results:
            status = "PASS" if r.passed else "FAIL"
            print(
                f"  [{status}] {r.scenario_id} / {r.payer_id} "
                f"(pack={r.guideline_pack} v{r.guideline_version}): "
                f"expected {r.expected}, got {r.actual}"
            )
        if report.all_passed:
            print("All validation scenarios passed.")
        else:
            print(f"{report.failed} check(s) failed.")

    return 0 if report.all_passed else 1


def _run_benchmark_command(args: argparse.Namespace) -> int:
    report = run_classifymymeds_benchmark(
        dataset_dir=args.dataset_dir,
        dim_pa_csv=args.dim_pa_csv,
        bridge_csv=args.bridge_csv,
        dim_claims_csv=args.dim_claims_csv,
        dim_date_csv=args.dim_date_csv,
        limit=args.limit,
    )
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(report.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        print(
            f"ClassifyMyMeds benchmark: {report.passed}/{report.total_cases} "
            f"cases passed ({report.accuracy:.0%} accuracy)."
        )
        print(
            "Imported "
            f"{report.import_summary.total_cases} case(s): "
            f"{report.import_summary.approved_cases} approved, "
            f"{report.import_summary.denied_cases} denied."
        )
        if report.failed:
            print("Mismatches:")
            for result in report.mismatches[:20]:
                reasons = ", ".join(result.failure_reasons) or "none"
                print(
                    f"  [FAIL] {result.case_id}: expected {result.expected.value}, "
                    f"predicted {result.predicted.value} ({reasons})"
                )
            remaining = report.failed - min(report.failed, 20)
            if remaining > 0:
                print(f"  ... {remaining} additional mismatch(es).")
            if not args.fail_on_mismatch:
                print(
                    "Benchmark completed; mismatches are reported but do not fail "
                    "the command unless --fail-on-mismatch is used."
                )
        elif report.total_cases:
            print("All benchmark cases passed.")

    if report.total_cases == 0:
        return 1
    if args.fail_on_mismatch:
        return 0 if report.failed == 0 else 1
    return 0


def _run_formulary_command(args: argparse.Namespace) -> int:
    output_path = None if args.no_write else args.output
    _, report = sync_davinci_formulary(
        source=args.source,
        source_type=args.source_type,
        output_path=output_path,
        resource_types=args.resource_types,
        timeout_seconds=args.timeout_seconds,
    )

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        print(
            f"Da Vinci formulary sync: {report.plans} plan(s), "
            f"{report.drugs} drug(s), {report.items} formulary item(s)."
        )
        print(
            f"PA required: {report.prior_authorization_required}; "
            f"step therapy required: {report.step_therapy_required}; "
            f"quantity limits: {report.quantity_limit}."
        )
        if report.output_path:
            print(f"Normalized catalog written to {report.output_path}.")

    return 0


def _run_clinical_accuracy_command(args: argparse.Namespace) -> int:
    scenarios = (
        load_seed_clinical_gold_set()
        if args.seed
        else load_adjudicated_clinical_gold_set(args.gold_set)
        if args.adjudicated
        else load_matrix_clinical_gold_set(args.gold_set)
    )
    calibration = None
    if args.calibration_input:
        calibration = ConfidenceCalibration.from_dict(
            json.loads(Path(args.calibration_input).read_text(encoding="utf-8"))
        )
    if args.calibrate_from_gold:
        calibration = build_confidence_calibration(
            scenarios,
            min_examples=args.calibration_min_examples,
        )
    if args.calibration_output:
        if calibration is None:
            calibration = build_confidence_calibration(
                scenarios,
                min_examples=args.calibration_min_examples,
            )
        Path(args.calibration_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.calibration_output).write_text(
            json.dumps(calibration.as_dict(), indent=2),
            encoding="utf-8",
        )

    policy = AutoDecisionPolicy(
        min_confidence=args.auto_threshold,
        require_traceability=not args.allow_untraceable,
        calibration=calibration,
    )
    report = evaluate_clinical_gold_set(
        scenarios,
        policy=policy,
        target_accuracy=args.target_accuracy,
    )
    payload = report.as_dict()
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Clinical auto-decision scorecard: "
            f"{report.auto_decided_accuracy:.2%} accuracy on "
            f"{report.auto_decided_total}/{report.total} auto-decided cases "
            f"({report.coverage_rate:.2%} coverage)."
        )
        print(
            f"Human-review overflow: {report.human_review_total}; "
            f"false approves: {report.false_approve_count}; "
            f"traceability: {report.traceability_rate:.2%}."
        )
        print(
            "Release gate: "
            + ("PASS" if report.passes_release_gate else "FAIL")
            + f" (target {report.target_accuracy:.2%})."
        )
        failures = [result for result in report.results if result.auto_decided and not result.correct]
        if failures:
            print("Auto-decided misses:")
            for result in failures[:20]:
                print(
                    f"  [MISS] {result.case_id}: expected {result.expected}, "
                    f"got {result.predicted}"
                )

    return 1 if args.fail_on_gate and not report.passes_release_gate else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
