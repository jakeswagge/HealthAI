"""CLI entry point for the validation runner (Final Milestone).

Usage:
    python -m validation.run            # run bundled scenarios, print summary
    python -m validation.run --json     # emit the full report as JSON

Exit code is 0 when all checks pass, 1 otherwise - so it can gate CI.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.models.governance import GovernanceSettings
from app.validation.runner import ValidationRunner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HealthAI validation runner")
    parser.add_argument(
        "--json", action="store_true", help="Emit the full report as JSON."
    )
    args = parser.parse_args(argv)

    runner = ValidationRunner(settings=GovernanceSettings())
    report = runner.run()

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(f"Validation: {report.passed}/{report.total} checks passed "
              f"({report.pass_rate:.0%}).")
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
