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
from app.services.gemini_client import GeminiClient
from app.services.local_client import LocalHeuristicClient
from app.services.mock_claude_client import MockClaudeClient, MockScenario
from app.services.factory import (
    describe_active_backend,
    describe_patient_details_backend,
    get_llm_client,
    get_patient_details_client,
)
from app.services.provider_router import (
    AITask,
    FallbackPolicy,
    ProviderTaskConfig,
    describe_task_backend,
    get_client_for_task,
    get_task_config,
)

__all__ = [
    "LLMClient",
    "LLMResponse",
    "LLMError",
    "AnthropicClient",
    "GeminiClient",
    "LocalHeuristicClient",
    "MockClaudeClient",
    "MockScenario",
    "get_llm_client",
    "get_patient_details_client",
    "describe_active_backend",
    "describe_patient_details_backend",
    "AITask",
    "FallbackPolicy",
    "ProviderTaskConfig",
    "get_client_for_task",
    "get_task_config",
    "describe_task_backend",
]
