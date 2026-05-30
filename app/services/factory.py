"""Backend selection for the LLM service layer.

Resolution order:
1. If ``HEALTHAI_LLM_BACKEND`` is set, honor it ("anthropic" or "local").
2. Otherwise, use Anthropic when ``ANTHROPIC_API_KEY`` is present and the SDK
   imports successfully.
3. Otherwise, fall back to the deterministic local heuristic backend.

This means the app runs out-of-the-box with no credentials (local backend),
and automatically upgrades to real Claude when a key is configured - with no
code changes anywhere else.
"""

from __future__ import annotations

import os

from app.services.llm_client import LLMClient, LLMError
from app.services.local_client import LocalHeuristicClient


def get_llm_client(force: str | None = None) -> LLMClient:
    """Return an :class:`LLMClient` instance based on configuration.

    Args:
        force: Optional explicit backend ("anthropic" or "local") that
            overrides environment-based detection.

    Returns:
        A ready-to-use LLM client. Falls back to the local backend if the
        Anthropic backend cannot be initialized.
    """
    choice = (force or os.environ.get("HEALTHAI_LLM_BACKEND", "")).strip().lower()

    if choice == "local":
        return LocalHeuristicClient()

    if choice == "anthropic":
        # Explicit request: let initialization errors surface to the caller.
        from app.services.anthropic_client import AnthropicClient

        return AnthropicClient()

    # Auto-detect: prefer Anthropic if a key exists, else local.
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from app.services.anthropic_client import AnthropicClient

            return AnthropicClient()
        except LLMError:
            # SDK missing or misconfigured: degrade gracefully to local.
            return LocalHeuristicClient()

    return LocalHeuristicClient()


def describe_active_backend(client: LLMClient | None = None) -> str:
    """Return a short human-readable description of the active backend."""
    client = client or get_llm_client()
    if client.is_ai:
        model = getattr(client, "model", "unknown")
        return f"Claude (Anthropic) — model: {model}"
    return "Local heuristic extractor (offline, no API key configured)"
