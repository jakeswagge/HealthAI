"""DocumentClassifier: assign a DocumentCategory to a document.

Wraps the existing keyword-based ``classify_document`` heuristic (from
``app.models.case_document``) and adds explicit manual-override support, so the
ingestion flow can auto-classify while still letting a human force a category.
This keeps a single source of truth for classification logic.
"""

from __future__ import annotations

from app.models.case_document import DocumentCategory, classify_document


class DocumentClassifier:
    """Classify documents into a :class:`DocumentCategory`."""

    def classify(
        self,
        filename: str | None,
        text: str | None,
        override: DocumentCategory | str | None = None,
    ) -> DocumentCategory:
        """Return the document category.

        Args:
            filename: Original filename (filename hints take precedence).
            text: Document text (content hints used if filename is ambiguous).
            override: Optional manual override that wins over auto-detection.
        """
        if override is not None:
            if isinstance(override, DocumentCategory):
                return override
            try:
                return DocumentCategory(str(override).strip().upper())
            except ValueError:
                return DocumentCategory.OTHER
        return classify_document(filename, text)
