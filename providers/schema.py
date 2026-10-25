"""
Gemini function declarations -> OpenAI tools.

The action and plugin registries describe every tool in Gemini's format:
uppercase types ("OBJECT", "STRING", "ARRAY", ...), "properties" and
"required" under "parameters". The OpenAI-compatible endpoints speak
JSON Schema: lowercase types, the same structure. This is the
translation — once, for every tool at once — so a provider rung can
call JARVIS's own skills exactly as the Gemini rungs do.
"""

from __future__ import annotations

_TYPE_MAP = {
    "STRING": "string",
    "INTEGER": "integer",
    "NUMBER": "number",
    "BOOLEAN": "boolean",
    "ARRAY": "array",
    "OBJECT": "object",
}


def _convert(schema: dict) -> dict:
    """One Gemini parameter schema -> one JSON Schema fragment."""
    schema = schema if isinstance(schema, dict) else {}
    out: dict = {}
    kind = str(schema.get("type") or "").upper()
    out["type"] = _TYPE_MAP.get(kind, kind.lower() or "string")
    if schema.get("description"):
        out["description"] = schema["description"]
    if schema.get("enum"):
        out["enum"] = schema["enum"]
    props = schema.get("properties")
    if isinstance(props, dict):
        out["properties"] = {k: _convert(v) for k, v in props.items()}
    items = schema.get("items")
    if isinstance(items, dict):
        out["items"] = _convert(items)
    return out


def to_openai(decls) -> list:
    """A list of Gemini function declarations -> a list of OpenAI tools."""
    tools = []
    for d in decls or []:
        params = d.get("parameters") or {"type": "OBJECT", "properties": {}}
        tools.append({
            "type": "function",
            "function": {
                "name": d.get("name", ""),
                "description": d.get("description", ""),
                "parameters": _convert(params),
            },
        })
    return tools
