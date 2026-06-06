"""GuidelinePackResolver: build a GuidelineRepository for a payer pack.

A "pack" is the DEFAULT guideline library (``data/guidelines/``) with optional
pack-specific overrides overlaid from ``data/guideline_packs/<PACK>/``. A
payer's pack therefore reuses every default guideline unless that pack ships a
file with the same ``guideline_id`` (or filename), in which case the pack's
version wins. This keeps payer policies small and avoids duplicating shared
content.

All packs use SIMPLIFIED MOCK policies; no proprietary payer content is used.
The resolver is deterministic, offline, and independent of the review engine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.guidelines.repository import GuidelineRepository
from app.models.clinical_guideline import ClinicalGuideline

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GUIDELINES_DIR = PROJECT_ROOT / "data" / "guidelines"
DEFAULT_PACKS_DIR = PROJECT_ROOT / "data" / "guideline_packs"

#: The pack id that maps directly to the base guideline library.
DEFAULT_PACK = "DEFAULT"


class GuidelinePackResolver:
    """Resolve a guideline-pack id to a :class:`GuidelineRepository`."""

    def __init__(
        self,
        base_dir: str | Path = DEFAULT_GUIDELINES_DIR,
        packs_dir: str | Path = DEFAULT_PACKS_DIR,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.packs_dir = Path(packs_dir)
        self._cache: dict[str, GuidelineRepository] = {}

    # ------------------------------------------------------------------ #
    # Loading helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_dir(directory: Path) -> list[ClinicalGuideline]:
        guidelines: list[ClinicalGuideline] = []
        if not directory.is_dir():
            return guidelines
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                guidelines.append(ClinicalGuideline.model_validate(data))
            except Exception as exc:  # noqa: BLE001 - skip bad files, keep going
                print(f"[packs] WARNING: skipping {path}: {exc}")
        return guidelines

    def available_packs(self) -> list[str]:
        """List pack ids: DEFAULT plus any subdirectory of the packs dir."""
        packs = [DEFAULT_PACK]
        if self.packs_dir.is_dir():
            for child in sorted(self.packs_dir.iterdir()):
                if child.is_dir():
                    packs.append(child.name.upper())
        return packs

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #
    def resolve(self, pack_id: str | None) -> GuidelineRepository:
        """Return a GuidelineRepository for the given pack id (cached).

        Unknown/empty pack ids fall back to the DEFAULT library. Pack-specific
        guidelines override base guidelines that share a ``guideline_id``.
        """
        key = (pack_id or DEFAULT_PACK).strip().upper()
        if key in self._cache:
            return self._cache[key]

        base = {g.guideline_id: g for g in self._load_dir(self.base_dir)}

        if key != DEFAULT_PACK:
            overlay_dir = self.packs_dir / key
            for g in self._load_dir(overlay_dir):
                base[g.guideline_id] = g  # pack overrides base by guideline_id

        repo = GuidelineRepository(list(base.values()))
        self._cache[key] = repo
        return repo


_DEFAULT_RESOLVER: Optional[GuidelinePackResolver] = None


def get_pack_resolver(force_reload: bool = False) -> GuidelinePackResolver:
    """Return a cached default :class:`GuidelinePackResolver`."""
    global _DEFAULT_RESOLVER
    if _DEFAULT_RESOLVER is None or force_reload:
        _DEFAULT_RESOLVER = GuidelinePackResolver()
    return _DEFAULT_RESOLVER
