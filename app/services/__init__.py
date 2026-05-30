"""Service layer for HealthAI.

All raw access to AI models lives here behind the :class:`LLMClient`
interface. Nothing outside this package should import an SDK or know which
backend is in use. This keeps the AI dependency isolated and swappable, and
lets the rest of the app (and the test suite) run deterministically offline.
"""

from app.services.llm_client import (
    LLMClient,
    LLMResponse,
    LLMError,
)
from app.services.anthropic_client import AnthropicClient
from app.services.local_client import LocalHeuristicClient
from app.services.mock_claude_client import MockClaudeClient, MockScenario
from app.services.factory import get_llm_client, describe_active_backend

__all__ = [
    "LLMClient",
    "LLMResponse",
    "LLMError",
    "AnthropicClient",
    "LocalHeuristicClient",
    "MockClaudeClient",
    "MockScenario",
    "get_llm_client",
    "describe_active_backend",
]
