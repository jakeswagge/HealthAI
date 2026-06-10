"""Task-based LLM provider routing.

Agents should ask for a capability (structured extraction, review reasoning,
appeal drafting, verification) rather than hardcoding a provider. This keeps
Gemini optional and leaves room for future OpenAI, Claude, DeepSeek, or local
providers behind the same ``LLMClient`` contract.
"""

from __future__ import annotations

import os
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.services.factory import get_llm_client
from app.services.llm_client import LLMClient


class AITask(str, Enum):
    """Supported AI task capabilities."""

    STRUCTURED_EXTRACTION = "structured_extraction"
    CLINICAL_REASONING = "clinical_reasoning"
    APPEAL_DRAFTING = "appeal_drafting"
    APPEAL_VERIFICATION = "appeal_verification"


class FallbackPolicy(str, Enum):
    """Behavior when the requested task backend is unavailable."""

    LOCAL = "local"
    DEFAULT = "default"
    ERROR = "error"


class ProviderTaskConfig(BaseModel):
    """Configuration for one task-specific provider route."""

    task: AITask
    provider_name: str = "auto"
    model_name: str | None = None
    enabled: bool = True
    fallback_policy: FallbackPolicy = FallbackPolicy.DEFAULT

    @field_validator("provider_name", mode="before")
    @classmethod
    def _provider(cls, v):
        value = "auto" if v is None else str(v).strip().lower()
        return value or "auto"


_TASK_ENV = {
    AITask.STRUCTURED_EXTRACTION: (
        "HEALTHAI_STRUCTURED_EXTRACTION_BACKEND",
        "HEALTHAI_PATIENT_DETAILS_BACKEND",
        "HEALTHAI_EXTRACTION_BACKEND",
    ),
    AITask.CLINICAL_REASONING: ("HEALTHAI_CLINICAL_REASONING_BACKEND",),
    AITask.APPEAL_DRAFTING: ("HEALTHAI_APPEAL_DRAFTING_BACKEND",),
    AITask.APPEAL_VERIFICATION: ("HEALTHAI_APPEAL_VERIFICATION_BACKEND",),
}


def _configured_provider(task: AITask) -> str:
    for env_name in _TASK_ENV.get(task, ()):
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip().lower()
    return ""


def get_task_config(task: AITask | str) -> ProviderTaskConfig:
    """Return the provider routing config for a task."""
    task_enum = task if isinstance(task, AITask) else AITask(str(task))
    provider = _configured_provider(task_enum)
    if not provider:
        provider = os.environ.get("HEALTHAI_LLM_BACKEND", "").strip().lower() or "auto"
    fallback_raw = os.environ.get(
        f"HEALTHAI_{task_enum.value.upper()}_FALLBACK", "default"
    )
    try:
        fallback = FallbackPolicy(fallback_raw.strip().lower())
    except ValueError:
        fallback = FallbackPolicy.DEFAULT
    enabled = os.environ.get(
        f"HEALTHAI_{task_enum.value.upper()}_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    return ProviderTaskConfig(
        task=task_enum,
        provider_name=provider,
        enabled=enabled,
        fallback_policy=fallback,
    )


def get_client_for_task(task: AITask | str) -> LLMClient:
    """Resolve an ``LLMClient`` for the requested AI task."""
    config = get_task_config(task)
    if not config.enabled:
        return get_llm_client(force="local")
    if config.provider_name in {"", "auto"}:
        return get_llm_client()
    return get_llm_client(force=config.provider_name)


def describe_task_backend(task: AITask | str, client: LLMClient | None = None) -> str:
    """Human-readable provider description for a task."""
    task_enum = task if isinstance(task, AITask) else AITask(str(task))
    client = client or get_client_for_task(task_enum)
    model = getattr(client, "model", getattr(client, "name", "unknown"))
    label = task_enum.value.replace("_", " ")
    if client.is_ai:
        return f"{label}: {getattr(client, 'name', 'unknown')} ({model})"
    return f"{label}: local heuristic"
