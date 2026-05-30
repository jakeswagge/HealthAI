"""Operational quality analytics.

The :class:`QualityAnalyticsEngine` computes evidence-quality and workflow
analytics from local storage (cases, evidence, quality assessments, reviewer
decisions, conflicts, audit). Read-only, on-demand, offline.
"""

from app.analytics.quality_analytics import (
    QualityAnalytics,
    QualityAnalyticsEngine,
)

__all__ = ["QualityAnalytics", "QualityAnalyticsEngine"]
