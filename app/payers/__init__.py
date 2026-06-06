"""Payer-specific guideline packs (Final Milestone).

Makes the platform configurable for different payer policies:

- :class:`~app.payers.repository.PayerRepository` loads :class:`PayerProfile`
  records from local JSON under ``data/payers/``.
- :class:`~app.payers.packs.GuidelinePackResolver` returns a
  :class:`GuidelineRepository` for a given pack id, overlaying pack-specific
  guideline JSON (``data/guideline_packs/<PACK>/``) on top of the DEFAULT
  library so a case can be reviewed under different payer policies.

All packs are SIMPLIFIED MOCK policies; no proprietary payer content is used.
Independent of the review/appeal agents (no import cycle).
"""

from app.payers.packs import GuidelinePackResolver, get_pack_resolver
from app.payers.repository import PayerRepository, get_payer_repository

__all__ = [
    "GuidelinePackResolver",
    "get_pack_resolver",
    "PayerRepository",
    "get_payer_repository",
]
