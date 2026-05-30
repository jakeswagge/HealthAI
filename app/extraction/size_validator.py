"""Large-document protection: detect and warn (no chunking, no RAG).

``DocumentSizeValidator`` measures a document's page count, character count,
and an estimated token count, then compares them against configurable
thresholds. It only *detects and warns* - it never truncates, splits, chunks,
or otherwise transforms the document. Callers (e.g. the Streamlit UI) decide
how to surface the warnings.

Token estimation
-----------------
We deliberately avoid a hard tokenizer dependency. A widely-used heuristic is
~4 characters per token for English text; we expose the divisor as a constant
so it can be tuned. This is an ESTIMATE for guardrail warnings only, not an
exact count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Average characters per token (rough heuristic for English prose/codes).
CHARS_PER_TOKEN = 4

# Default thresholds. These are warning levels, not hard limits.
DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_CHARS = 60_000
DEFAULT_MAX_TOKENS = 15_000


class SizeSeverity(str, Enum):
    """Severity of a size assessment."""

    OK = "ok"
    WARNING = "warning"


@dataclass
class SizeThresholds:
    """Configurable thresholds for the size validator."""

    max_pages: int = DEFAULT_MAX_PAGES
    max_chars: int = DEFAULT_MAX_CHARS
    max_tokens: int = DEFAULT_MAX_TOKENS


@dataclass
class DocumentSizeReport:
    """Result of a size assessment."""

    page_count: int
    char_count: int
    estimated_tokens: int
    severity: SizeSeverity
    warnings: list[str] = field(default_factory=list)
    thresholds: SizeThresholds = field(default_factory=SizeThresholds)

    @property
    def exceeds_threshold(self) -> bool:
        return self.severity is SizeSeverity.WARNING

    def as_dict(self) -> dict:
        return {
            "page_count": self.page_count,
            "char_count": self.char_count,
            "estimated_tokens": self.estimated_tokens,
            "severity": self.severity.value,
            "warnings": list(self.warnings),
            "thresholds": {
                "max_pages": self.thresholds.max_pages,
                "max_chars": self.thresholds.max_chars,
                "max_tokens": self.thresholds.max_tokens,
            },
        }


def estimate_tokens(char_count: int, chars_per_token: int = CHARS_PER_TOKEN) -> int:
    """Estimate token count from a character count (ceiling division)."""
    if char_count <= 0:
        return 0
    cpt = max(1, chars_per_token)
    return (char_count + cpt - 1) // cpt


class DocumentSizeValidator:
    """Measure document size and warn when thresholds are exceeded."""

    def __init__(self, thresholds: SizeThresholds | None = None) -> None:
        self.thresholds = thresholds or SizeThresholds()

    def assess(self, text: str, page_count: int = 1) -> DocumentSizeReport:
        """Assess raw text + page count and return a size report.

        Args:
            text: The extracted document text.
            page_count: Number of pages (TXT defaults to 1).

        Returns:
            A :class:`DocumentSizeReport`. ``severity`` is WARNING if any
            threshold is exceeded, otherwise OK.
        """
        text = text or ""
        char_count = len(text)
        tokens = estimate_tokens(char_count)

        warnings: list[str] = []
        t = self.thresholds

        if page_count > t.max_pages:
            warnings.append(
                f"Document has {page_count} pages, exceeding the recommended "
                f"limit of {t.max_pages}. Processing may be slow or hit model "
                f"context limits."
            )
        if char_count > t.max_chars:
            warnings.append(
                f"Document has {char_count:,} characters, exceeding the "
                f"recommended limit of {t.max_chars:,}. Consider reviewing a "
                f"smaller excerpt."
            )
        if tokens > t.max_tokens:
            warnings.append(
                f"Estimated ~{tokens:,} tokens, exceeding the recommended "
                f"limit of {t.max_tokens:,}. This may approach the model's "
                f"context window and increase cost/latency."
            )

        severity = SizeSeverity.WARNING if warnings else SizeSeverity.OK
        return DocumentSizeReport(
            page_count=page_count,
            char_count=char_count,
            estimated_tokens=tokens,
            severity=severity,
            warnings=warnings,
            thresholds=t,
        )

    def assess_document(self, document) -> DocumentSizeReport:
        """Convenience: assess an :class:`ExtractedDocument`-like object."""
        text = getattr(document, "text", "") or ""
        page_count = getattr(document, "page_count", 1) or 1
        return self.assess(text, page_count)
