"""PayerRepository: load payer profiles from local JSON.

Profiles live under ``data/payers/*.json``. A built-in DEFAULT profile is always
available even if no files are present, so the platform works out of the box.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.models.payer import PayerProfile, PayerStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PAYERS_DIR = PROJECT_ROOT / "data" / "payers"


#: Always-present fallback so a fresh install has at least the DEFAULT payer.
_BUILTIN_DEFAULT = PayerProfile(
    payer_id="DEFAULT",
    payer_name="HealthAI Default Policy",
    guideline_pack="DEFAULT",
    version="2026.1",
    status=PayerStatus.ACTIVE,
)


class PayerRepository:
    """In-memory repository of payer profiles loaded from JSON."""

    def __init__(self, profiles: list[PayerProfile] | None = None) -> None:
        self._profiles: dict[str, PayerProfile] = {}
        # Seed the built-in default, then overlay any provided profiles.
        self._profiles[_BUILTIN_DEFAULT.payer_id] = _BUILTIN_DEFAULT
        for p in profiles or []:
            self._profiles[p.payer_id] = p

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    @classmethod
    def from_directory(cls, directory: str | Path) -> "PayerRepository":
        """Load all ``*.json`` payer profiles from a directory."""
        directory = Path(directory)
        profiles: list[PayerProfile] = []
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    profiles.append(PayerProfile.model_validate(data))
                except Exception as exc:  # noqa: BLE001 - skip bad files, keep going
                    print(f"[payers] WARNING: skipping {path.name}: {exc}")
        else:
            print(f"[payers] WARNING: directory not found: {directory}")
        return cls(profiles)

    # ------------------------------------------------------------------ #
    # Access
    # ------------------------------------------------------------------ #
    def all(self) -> list[PayerProfile]:
        return list(self._profiles.values())

    def active(self) -> list[PayerProfile]:
        return [p for p in self._profiles.values() if p.is_active]

    def get(self, payer_id: str) -> Optional[PayerProfile]:
        return self._profiles.get((payer_id or "").strip().upper())

    def get_or_default(self, payer_id: str | None) -> PayerProfile:
        """Return the requested payer, or the DEFAULT profile as a fallback."""
        if payer_id:
            found = self.get(payer_id)
            if found is not None:
                return found
        return self._profiles["DEFAULT"]

    def __len__(self) -> int:
        return len(self._profiles)


_DEFAULT_REPO: Optional[PayerRepository] = None


def get_payer_repository(force_reload: bool = False) -> PayerRepository:
    """Return a cached repository loaded from the default payers dir."""
    global _DEFAULT_REPO
    if _DEFAULT_REPO is None or force_reload:
        _DEFAULT_REPO = PayerRepository.from_directory(DEFAULT_PAYERS_DIR)
    return _DEFAULT_REPO
