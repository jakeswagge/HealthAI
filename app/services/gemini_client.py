"""Google Gemini backend for the LLM service layer.

Uses the modern ``google-genai`` SDK. By default the adapter targets Vertex AI
with Application Default Credentials (ADC), avoiding AI Studio API-key billing
paths. AI Studio can still be forced for local experiments by setting
``HEALTHAI_GEMINI_USE_VERTEXAI=false`` and providing ``GEMINI_API_KEY``.
"""

from __future__ import annotations

import os
from typing import Any

from app.services.llm_client import LLMClient, LLMError, LLMResponse


DEFAULT_VERTEX_PROJECT = "gen-lang-client-0121983409"
DEFAULT_VERTEX_LOCATION = "us-central1"
DEFAULT_MODEL = os.environ.get("HEALTHAI_GEMINI_MODEL", "gemini-2.5-flash")


def _env_truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class GeminiClient(LLMClient):
    """LLM backend backed by the Google Gemini API."""

    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        use_vertexai: bool | None = None,
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        self.model = model
        self.use_vertexai = (
            _env_truthy(os.environ.get("HEALTHAI_GEMINI_USE_VERTEXAI"), default=True)
            if use_vertexai is None
            else use_vertexai
        )
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT") or DEFAULT_VERTEX_PROJECT
        self.location = (
            location
            or os.environ.get("GOOGLE_CLOUD_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_REGION")
            or DEFAULT_VERTEX_LOCATION
        )
        self._api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )

        if self.use_vertexai:
            os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
            os.environ["GOOGLE_CLOUD_PROJECT"] = self.project
            os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.location)
        elif not self._api_key:
            raise LLMError(
                "GEMINI_API_KEY or GOOGLE_API_KEY is not set; cannot use the "
                "Gemini AI Studio backend. For Vertex AI, leave "
                "HEALTHAI_GEMINI_USE_VERTEXAI unset or set it to true and "
                "authenticate with Application Default Credentials."
            )

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise LLMError(
                "The 'google-genai' package is not installed. Install it with "
                "`pip install google-genai` to use the Gemini backend."
            ) from exc

        self._types = types
        self._client = genai.Client() if self.use_vertexai else genai.Client(api_key=self._api_key)

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
        contents = self._format_messages(messages)
        config = self._types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

        try:
            resp = self._client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
        except Exception as exc:  # pragma: no cover - network/credentials
            raise LLMError(f"Gemini request failed: {exc}") from exc

        text = self._extract_text(resp)
        if not text:
            raise LLMError("Gemini returned an empty response.")

        return LLMResponse(
            text=text,
            model=self.model,
            raw={
                "response_id": getattr(resp, "id", None),
                "model_version": getattr(resp, "model_version", None),
                "vertexai": self.use_vertexai,
                "project": self.project if self.use_vertexai else None,
                "location": self.location if self.use_vertexai else None,
            },
        )

    @staticmethod
    def _format_messages(messages: list[dict[str, str]]) -> str:
        parts: list[str] = []
        for message in messages:
            role = str(message.get("role", "user")).strip().lower()
            content = str(message.get("content", "")).strip()
            if not content:
                continue
            label = "Assistant" if role == "assistant" else "User"
            parts.append(f"{label}:\n{content}")

        if not parts:
            raise LLMError("No message content supplied to the Gemini backend.")
        return "\n\n".join(parts)

    @staticmethod
    def _extract_text(resp: Any) -> str:
        text = getattr(resp, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()

        parts: list[str] = []
        for candidate in getattr(resp, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    parts.append(part_text)
        return "".join(parts).strip()
