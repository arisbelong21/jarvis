"""
Provider registry: build(pid, config) -> an LLM.

One kind today — "openai", the chat-completions dialect that OpenAI,
the aggregators and the local runtimes all speak, served by
providers/llm_openai.py. Adding another kind later means one more
branch here and one more module beside llm_openai; callers (the ladder
in core/gemini.py, the cloud brain in cloud.py) never change.
"""

from __future__ import annotations


def build(pid: str, config: dict):
    """An LLM object exposing .complete(messages, tools=None, timeout=120)
    -> (text, calls). Raises for an unknown kind or an incomplete
    configuration, which the ladder treats as a failed rung."""
    config = config or {}
    kind = str(config.get("kind") or "openai").strip().lower()
    if kind in ("", "openai", "openai_compatible", "openai-compatible"):
        from providers.llm_openai import OpenAILLM
        return OpenAILLM(pid, config)
    raise RuntimeError(f"provider '{pid}' has unknown kind '{kind}'")
