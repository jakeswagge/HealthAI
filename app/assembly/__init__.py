"""Multi-document case assembly.

The :class:`CaseAssemblyEngine` combines evidence from many
:class:`CaseDocument` objects into a single :class:`UnifiedCaseContext`:
de-duplicated evidence, a resolved value per fact, a conflict report, and a
list of missing information. It also synthesizes a back-compatible
:class:`PatientCase` (with per-field source attribution) so the existing
review and appeal engines keep working unchanged.

Independent of extraction, review, appeals, and audit.
"""

from app.assembly.engine import CaseAssemblyEngine

__all__ = ["CaseAssemblyEngine"]
