"""Evidence extraction and traceability.

Turns a :class:`CaseDocument`'s raw text into source-backed
:class:`EvidenceReference` objects: each captures a normalized fact, the exact
page, the section label, and a verbatim quote. This is the deterministic,
offline backbone of traceability - independent of extraction, review, appeals,
and audit.
"""

from app.evidence.extractor import (
    EvidenceExtractor,
    FACT_TYPES,
)
from app.evidence.repository import EvidenceRepository
from app.evidence.linker import link_review, link_appeal

__all__ = [
    "EvidenceExtractor",
    "FACT_TYPES",
    "EvidenceRepository",
    "link_review",
    "link_appeal",
]
