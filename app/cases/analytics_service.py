"""AnalyticsService: org-wide quality + workflow analytics (M11).

Extracted from ``CaseService`` during the Milestone 12 facade decomposition.
A thin wrapper over :class:`QualityAnalyticsEngine` so the facade exposes
``quality_analytics()`` without owning the engine wiring directly.

Behavior is identical to the original CaseService method - a cohesion
extraction only.
"""

from __future__ import annotations

from app.analytics.quality_analytics import QualityAnalytics, QualityAnalyticsEngine


class AnalyticsService:
    """Compute organization-wide quality + workflow analytics."""

    def __init__(self, analytics: QualityAnalyticsEngine) -> None:
        self.analytics = analytics

    def quality_analytics(self) -> QualityAnalytics:
        """Compute org-wide quality + workflow analytics."""
        return self.analytics.collect()
