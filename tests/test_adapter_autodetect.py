"""Covers auto-detection of whichever API key is present, in the required
priority order, and that no key falls back to None (MockAdapter territory)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo"))

import pytest

import run_demo  # noqa: E402

ALL_KEYS = [env_var for env_var, _, _ in run_demo._LIVE_ADAPTER_CANDIDATES]


@pytest.fixture(autouse=True)
def _clear_all_provider_keys(monkeypatch):
    for var in ALL_KEYS:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize(
    "env_var,expected_model",
    [
        ("GEMINI_API_KEY", "gemini/gemini-3.1-flash-lite"),
        ("DEEPSEEK_API_KEY", "deepseek/deepseek-chat"),
        ("QWEN_API_KEY", "dashscope/qwen-turbo"),
        ("OPENAI_API_KEY", "gpt-4o-mini"),
        ("GROQ_API_KEY", "groq/qwen/qwen3.8-27b"),
    ],
)
def test_each_key_alone_selects_its_own_model(monkeypatch, env_var, expected_model):
    monkeypatch.setenv(env_var, "dummy-value")
    adapter = run_demo._detect_live_adapter()
    assert adapter is not None
    assert adapter.model == expected_model
    assert adapter.api_key_env == env_var


def test_no_key_returns_none():
    assert run_demo._detect_live_adapter() is None


def test_priority_order_gemini_beats_everything_else(monkeypatch):
    for var in ALL_KEYS:
        monkeypatch.setenv(var, "dummy-value")
    adapter = run_demo._detect_live_adapter()
    assert adapter.api_key_env == "GEMINI_API_KEY"


def test_priority_order_deepseek_beats_qwen_and_openai_and_groq(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    monkeypatch.setenv("QWEN_API_KEY", "q")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    adapter = run_demo._detect_live_adapter()
    assert adapter.api_key_env == "DEEPSEEK_API_KEY"


def test_priority_order_qwen_beats_openai_and_groq(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "q")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    adapter = run_demo._detect_live_adapter()
    assert adapter.api_key_env == "QWEN_API_KEY"


def test_litellm_adapter_receives_custom_env_var_key_explicitly(monkeypatch):
    """The actual mechanism that makes QWEN_API_KEY work even though litellm's
    own convention for a dashscope/ model expects DASHSCOPE_API_KEY: the
    adapter passes the key explicitly via the `api_key` kwarg rather than
    relying on litellm's internal env-var lookup."""
    monkeypatch.setenv("QWEN_API_KEY", "my-qwen-key-value")
    from harness.model_adapter.litellm_adapter import LiteLLMAdapter

    adapter = LiteLLMAdapter(model="dashscope/qwen-turbo", api_key_env="QWEN_API_KEY")
    assert adapter.api_key_env == "QWEN_API_KEY"
    assert os.environ.get(adapter.api_key_env) == "my-qwen-key-value"
