"""Sutra evaluation configuration.

This file is the single, official location for setting which model family and
provider string AI_API_KEY should target during evaluation.

The hackathon evaluation guidelines state that evaluators will run:
    export AI_API_KEY="<PROVIDED_API_KEY>"
    make setup
    make run

Because AI_API_KEY is provider-agnostic, Sutra routes it to the model defined
below (or overridden via the optional AI_MODEL environment variable).

>>> SET THIS TO MATCH THE OFFICIALLY PRESCRIBED MODEL BEFORE SUBMISSION <<<
"""
from __future__ import annotations

import os

# ==============================================================================
# OFFICIALLY PRESCRIBED MODEL CONFIGURATION
# Update this single line once the hackathon organisers announce the model:
# Common LiteLLM model strings:
#   - Google Gemini:  "gemini/gemini-3.1-flash-lite"
#   - OpenAI:         "gpt-4o-mini" or "gpt-4o"
#   - Anthropic:      "claude-3-5-sonnet-20241022" or "claude-3-5-haiku-20241022"
#   - DeepSeek:       "deepseek/deepseek-chat"
#   - Groq:           "groq/llama-3.3-70b-versatile"
# ==============================================================================
DEFAULT_AI_MODEL = "gemini/gemini-3.1-flash-lite"
DEFAULT_AI_LABEL = "Evaluation Model"


def get_eval_model() -> tuple[str, str]:
    """Returns (model_string, display_label) configured for AI_API_KEY.
    Allows runtime override via AI_MODEL env var if desired.
    """
    model = os.environ.get("AI_MODEL", DEFAULT_AI_MODEL).strip()
    label = f"AI_API_KEY ({model})"
    return model, label
