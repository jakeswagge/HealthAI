"""Anthropic Claude backend for the LLM service layer.

This is the real-AI implementation. It is used automatically when an
``ANTHROPIC_API_KEY`` or ``ANTHROPIC_AUTH_TOKEN`` is configured and the
``anthropic`` SDK is installed.
The agent code never imports the SDK directly - only this module does.

Model selection (highest preference first) targets Claude Opus, configurable
via the ``HEALTHAI_CLAUDE_MODEL`` environment variable.
"""

from __future__ import annotations

import os

from app.services.llm_client import LLMClient, LLMError, LLMResponse

# Default to the latest Claude Opus generation; override via env if needed.
DEFAULT_MODEL = os.environ.get("HEALTHAI_CLAUDE_MODEL", "claude-opus-4-8")


class AnthropicClient(LLMClient):
    """LLM backend backed by the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.model = model
        self._api_key = (
            api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )
        if not self._api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN is not set; cannot "
                "use the Anthropic backend."
            )

        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise LLMError(
                "The 'anthropic' package is not installed. Install it with "
                "`pip install anthropic` to use the Claude backend."
            ) from exc

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=self._api_key)

    @property
    def is_ai(self) -> bool:
        return True

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> LLMResponse:
        try:
            resp = self._client.messages.create(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:  # pragma: no cover - network/credentials
            raise LLMError(f"Anthropic request failed: {exc}") from exc

        # Concatenate all text blocks from the response content.
        parts: list[str] = []
        for block in getattr(resp, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        text = "".join(parts).strip()

        if not text:
            raise LLMError("Anthropic returned an empty response.")

        return LLMResponse(text=text, model=self.model, raw={"id": getattr(resp, "id", None)})
