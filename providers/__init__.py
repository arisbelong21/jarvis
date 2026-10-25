"""
External model providers for ARIS.

WHAT LIVES HERE
    registry.py   — build(pid, config) -> an LLM object, by provider kind
    llm_openai.py — the OpenAI-compatible chat-completions adapter
    schema.py     — Gemini function declarations -> OpenAI tools

WHY
    core/gemini.py walks a ladder of models and falls through them on
    quota, outage and 404. A provider is another rung on that same
    ladder — see _provider_rungs there — so a strong coder model can
    lead the SMART tier while every Gemini name stays behind it as a
    fallback. Nothing here is imported at startup; the rungs are read
    from config on each call, and with no provider configured this
    package is never touched and behaviour is exactly as before.

THE DIALECT
    The chat-completions API that has become the de-facto standard:
    OpenAI itself, and the aggregators and local runtimes that copy it
    (DeepSeek, Qwen, Groq, Together, OpenRouter, Ollama, LM Studio,
    FreeLLM-style gateways, ...). One base_url + api_key + model name
    is the entire configuration, stored per provider id in
    config/api_keys.json under "providers" — see memory/config_manager.
"""
