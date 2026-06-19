"""Tests for the Gemini LLM backend adapter."""

from __future__ import annotations

import sys
import types
import os

import pytest

import app.services.factory as factory
from app.services.factory import (
    describe_active_backend,
    describe_patient_details_backend,
    get_llm_client,
    get_patient_details_client,
)
from app.services.gemini_client import GeminiClient
from app.services.llm_client import LLMError
from app.services.local_client import LocalHeuristicClient


def _install_fake_google_genai(monkeypatch, response=None, raise_error=None) -> dict:
    calls: dict = {}

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeThinkingConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeModels:
        def generate_content(self, **kwargs):
            calls["generate_content"] = kwargs
            if raise_error is not None:
                raise raise_error
            return response or types.SimpleNamespace(text='{"ok": true}', id="resp-1")

    class FakeClient:
        def __init__(self, api_key=None):
            calls["api_key"] = api_key
            self.models = FakeModels()

    google_pkg = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    genai_mod.Client = FakeClient
    genai_mod.types = types.SimpleNamespace(
        GenerateContentConfig=FakeGenerateContentConfig,
        ThinkingConfig=FakeThinkingConfig,
    )
    google_pkg.genai = genai_mod

    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    return calls


def test_gemini_client_complete_uses_google_genai_config(monkeypatch):
    calls = _install_fake_google_genai(monkeypatch)

    client = GeminiClient(api_key="test-key", model="gemini-test", use_vertexai=False)
    resp = client.complete(
        system="system prompt",
        messages=[
            {"role": "user", "content": "extract this"},
            {"role": "assistant", "content": "bad json"},
            {"role": "user", "content": "try again"},
        ],
        max_tokens=321,
        temperature=0.2,
    )

    assert resp.text == '{"ok": true}'
    assert resp.model == "gemini-test"
    assert calls["api_key"] == "test-key"

    request = calls["generate_content"]
    assert request["model"] == "gemini-test"
    assert "User:\nextract this" in request["contents"]
    assert "Assistant:\nbad json" in request["contents"]
    assert "User:\ntry again" in request["contents"]
    assert request["config"].kwargs == {
        "system_instruction": "system prompt",
        "max_output_tokens": 321,
        "temperature": 0.2,
        "thinking_config": request["config"].kwargs["thinking_config"],
    }
    assert request["config"].kwargs["thinking_config"].kwargs == {
        "thinking_budget": 0,
        "include_thoughts": False,
    }


def test_gemini_client_defaults_to_vertex_adc(monkeypatch):
    calls = _install_fake_google_genai(monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("HEALTHAI_GEMINI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

    client = GeminiClient(model="gemini-3.5-flash")

    assert client.use_vertexai is True
    assert calls["api_key"] is None
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "gen-lang-client-0121983409"
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "global"


def test_gemini_client_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("HEALTHAI_GEMINI_USE_VERTEXAI", "false")

    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiClient()


def test_gemini_client_extracts_candidate_part_text(monkeypatch):
    response = types.SimpleNamespace(
        text=None,
        candidates=[
            types.SimpleNamespace(
                content=types.SimpleNamespace(
                    parts=[
                        types.SimpleNamespace(text='{"from": '),
                        types.SimpleNamespace(text='"parts"}'),
                    ]
                )
            )
        ],
    )
    _install_fake_google_genai(monkeypatch, response=response)

    resp = GeminiClient(api_key="test-key").complete(
        system="system",
        messages=[{"role": "user", "content": "content"}],
    )

    assert resp.text == '{"from": "parts"}'


def test_gemini_client_wraps_provider_errors(monkeypatch):
    _install_fake_google_genai(monkeypatch, raise_error=RuntimeError("network down"))

    client = GeminiClient(api_key="test-key", use_vertexai=False)
    with pytest.raises(LLMError, match="Gemini request failed: network down"):
        client.complete(system="system", messages=[{"role": "user", "content": "x"}])


def test_factory_force_gemini_returns_gemini_client(monkeypatch):
    _install_fake_google_genai(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    monkeypatch.setenv("HEALTHAI_GEMINI_USE_VERTEXAI", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    client = get_llm_client(force="gemini")

    assert isinstance(client, GeminiClient)
    assert client.is_ai is True
    assert describe_active_backend(client) == "Gemini (Google) — model: gemini-3.5-flash"


def test_factory_auto_detects_gemini_when_only_gemini_key_exists(monkeypatch):
    _install_fake_google_genai(monkeypatch)
    monkeypatch.delenv("HEALTHAI_LLM_BACKEND", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(factory, "_google_adc_available", lambda: False)
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    monkeypatch.setenv("HEALTHAI_GEMINI_USE_VERTEXAI", "false")

    client = get_llm_client()

    assert isinstance(client, GeminiClient)


def test_factory_auto_detects_gemini_with_vertex_adc(monkeypatch):
    _install_fake_google_genai(monkeypatch)
    monkeypatch.delenv("HEALTHAI_LLM_BACKEND", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("HEALTHAI_GEMINI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(factory, "_google_adc_available", lambda: True)

    client = get_llm_client()

    assert isinstance(client, GeminiClient)
    assert client.use_vertexai is True


def test_patient_details_uses_task_configured_provider(monkeypatch):
    _install_fake_google_genai(monkeypatch)
    monkeypatch.setenv("HEALTHAI_STRUCTURED_EXTRACTION_BACKEND", "gemini")
    monkeypatch.setenv("HEALTHAI_GEMINI_USE_VERTEXAI", "false")
    monkeypatch.delenv("HEALTHAI_PATIENT_DETAILS_BACKEND", raising=False)
    monkeypatch.delenv("HEALTHAI_EXTRACTION_BACKEND", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

    client = get_patient_details_client()

    assert isinstance(client, GeminiClient)
    assert describe_patient_details_backend(client).startswith("Gemini (Google)")
    assert "gemini-3.5-flash" in describe_patient_details_backend(client)


def test_factory_gemini_auto_detection_degrades_to_local_without_sdk(monkeypatch):
    monkeypatch.delenv("HEALTHAI_LLM_BACKEND", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(factory, "_google_adc_available", lambda: False)
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.delitem(sys.modules, "google.genai", raising=False)

    client = get_llm_client()

    assert isinstance(client, LocalHeuristicClient)
