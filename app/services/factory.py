"""Backend selection for the LLM service layer.

Resolution order:
1. If ``HEALTHAI_LLM_BACKEND`` is set, honor it ("anthropic", "gemini", or
   "local").
2. Otherwise, use Anthropic when ``ANTHROPIC_API_KEY`` /
   ``ANTHROPIC_AUTH_TOKEN`` is present and the SDK imports successfully.
3. Otherwise, use Gemini when Vertex mode is enabled or
   ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` is present and the SDK imports
   successfully.
4. Otherwise, fall back to the deterministic local heuristic backend.

This means the app runs out-of-the-box with no credentials (local backend),
and automatically upgrades to a configured hosted LLM backend with no code
changes anywhere else.
"""

from __future__ import annotations

import os

from app.services.llm_client import LLMClient, LLMError
from app.services.local_client import LocalHeuristicClient


def get_llm_client(force: str | None = None) -> LLMClient:
    """Return an :class:`LLMClient` instance based on configuration.

    Args:
        force: Optional explicit backend ("anthropic", "gemini", or "local") that
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

    if choice == "gemini":
        # Explicit request: let initialization errors surface to the caller.
        from app.services.gemini_client import GeminiClient

        return GeminiClient()

    # Auto-detect: prefer Anthropic, then Gemini, then local.
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        try:
            from app.services.anthropic_client import AnthropicClient

            return AnthropicClient()
        except LLMError:
            # SDK missing or misconfigured: degrade gracefully to local.
            return LocalHeuristicClient()

    # Auto-selection should use HealthAI configuration, not the SDK transport
    # flag that GeminiClient sets internally after an explicit Gemini request.
    gemini_vertex_enabled = os.environ.get(
        "HEALTHAI_GEMINI_USE_VERTEXAI",
        "",
    ).strip().lower() in {"1", "true", "yes", "on"}
    if (
        gemini_vertex_enabled
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    ):
        try:
            from app.services.gemini_client import GeminiClient

            return GeminiClient()
        except LLMError:
            # SDK missing or misconfigured: degrade gracefully to local.
            return LocalHeuristicClient()

    return LocalHeuristicClient()


def get_patient_details_client(force: str | None = None) -> LLMClient:
    """Return the configured backend for structured patient-detail extraction.

    Backward-compatible wrapper for dashboard/tests. Without a task-specific
    override this uses the normal provider auto-detection. Gemini remains
    optional and can be selected with ``HEALTHAI_STRUCTURED_EXTRACTION_BACKEND``,
    ``HEALTHAI_PATIENT_DETAILS_BACKEND``, or ``HEALTHAI_LLM_BACKEND``.
    """
    choice = (
        force
        or os.environ.get("HEALTHAI_STRUCTURED_EXTRACTION_BACKEND")
        or os.environ.get("HEALTHAI_PATIENT_DETAILS_BACKEND")
        or os.environ.get("HEALTHAI_EXTRACTION_BACKEND")
        or ""
    ).strip().lower()

    if choice:
        return get_llm_client(force=choice)

    return get_llm_client()


def describe_active_backend(client: LLMClient | None = None) -> str:
    """Return a short human-readable description of the active backend."""
    client = client or get_llm_client()
    if client.is_ai:
        model = getattr(client, "model", "unknown")
        if getattr(client, "name", "") == "anthropic":
            return f"Claude (Anthropic) — model: {model}"
        if getattr(client, "name", "") == "gemini":
            return f"Gemini (Google) — model: {model}"
        return f"AI backend ({getattr(client, 'name', 'unknown')}) — model: {model}"
    return "Local heuristic extractor (offline, no API key configured)"


def describe_patient_details_backend(client: LLMClient | None = None) -> str:
    """Return the backend used for structured patient-detail extraction."""
    return describe_active_backend(client or get_patient_details_client())
