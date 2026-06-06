"""Validation datasets + automated runner (Final Milestone).

This top-level package holds the sample payer datasets under ``datasets/`` and
re-exports the runner implemented in :mod:`app.validation` so it can be invoked
as ``python -m validation.run`` or imported directly.

All data is mock/synthetic; no PHI and no proprietary payer content.
"""

from app.validation.runner import (
    ValidationReport,
    ValidationResult,
    ValidationRunner,
    load_default_scenarios,
)

__all__ = [
    "ValidationReport",
    "ValidationResult",
    "ValidationRunner",
    "load_default_scenarios",
]
