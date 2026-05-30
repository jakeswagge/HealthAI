"""Clinical guideline library.

Loads guideline definitions from local JSON (no database) and provides lookup
and matching against a :class:`PatientCase`. This package is independent of the
extraction and review engines - it only knows about guideline data.
"""

from app.guidelines.repository import (
    GuidelineRepository,
    GuidelineMatch,
    get_default_repository,
)

__all__ = [
    "GuidelineRepository",
    "GuidelineMatch",
    "get_default_repository",
]
