"""Reviewer feedback collection + learning dataset (no ML).

Stores structured reviewer feedback on extraction/review/appeal/assembly
results and exposes a :class:`FeedbackDataset` that aggregates feedback,
conflict resolutions, and (optionally) appeal feedback into an exportable
learning dataset. This milestone collects data only - it never retrains or
runs any machine learning.
"""

from app.feedback.repository import ReviewerFeedbackRepository
from app.feedback.dataset import FeedbackDataset

__all__ = ["ReviewerFeedbackRepository", "FeedbackDataset"]
