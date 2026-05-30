"""Abstract LLM client interface used by agents.

Agents depend on this interface only. Concrete backends (Anthropic Claude, a
local heuristic fallback, or any future provider) implement it. This is the
single seam where AI is isolated from the rest of the application.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


class LLMError(Exception):
    """Raised when an LLM backend fails to produce a usable response."""


@dataclass
class LLMResponse:
    """A normalized response from any LLM backend.

    Attributes:
        text: The raw text content returned by the model. For extraction this
            is expected to be a JSON object as a string.
        model: Identifier of the model/backend that produced the response.
        raw: Optional provider-specific payload for debugging.
    """

    text: str
    model: str
    raw: dict = field(default_factory=dict)


class LLMClient(abc.ABC):
    """Interface every LLM backend must implement."""

    #: Human-readable backend name (e.g. "anthropic", "local-heuristic").
    name: str = "abstract"

    @abc.abstractmethod
    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Run a single completion request.

        Args:
            system: System prompt.
            messages: Chat messages (list of {"role", "content"}).
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0.0 = deterministic).

        Returns:
            An :class:`LLMResponse`.

        Raises:
            LLMError: If the backend cannot produce a response.
        """
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def is_ai(self) -> bool:
        """True if this backend calls a real AI model, False for heuristics."""
        raise NotImplementedError
