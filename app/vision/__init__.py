"""Vision-based evidence extraction.

The :class:`VisionEvidenceExtractor` turns OCR page results into
:class:`EvidenceReference` objects using the SAME contract as the text-based
:class:`app.evidence.extractor.EvidenceExtractor`. It preserves the source
document, page number, verbatim quoted text, and folds the OCR confidence into
each reference's confidence so downstream consumers can see OCR uncertainty.

It never fabricates text: it only extracts facts from the OCR output it is
given. Independent of review/appeal/audit.
"""

from app.vision.extractor import VisionEvidenceExtractor

__all__ = ["VisionEvidenceExtractor"]
