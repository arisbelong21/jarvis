"""
mcp_schema.py — translate an MCP tool's JSON Schema into the subset Gemini's
function declarations actually accept.

WHY THIS FILE EXISTS
    MCP servers publish real JSON Schema: `$ref` into `$defs`, `const`, `oneOf`,
    `anyOf`, `additionalProperties`, `pattern`, `exclusiveMinimum`. Gemini's
    function declarations are an OpenAPI-3.0-flavoured subset — none of those
    keywords exist there, and the SDK does not ignore them: it raises, once per
    occurrence, while building the session config. REA alone ships 493 `$ref`s
    and 1035 `const`s across its 207 tools, which came out as 759 validation
    errors and a session that could not be built at all.

    So the schema is TRANSLATED, not passed through:

      $ref          resolved against the root $defs when it is a local pointer
                    (so the model still learns the real fields); anything
                    unresolvable becomes a permissive object
      const         becomes enum:[value] — the same constraint, expressed in a
                    keyword that survives
      oneOf/anyOf   first branch wins (the alternatives are usually
                    "identifier | address"; the first is the common case)
      allOf         branches merged
      type: [x,y]   first non-null type, plus nullable when null was present
      everything    dropped: additionalProperties, pattern, exclusiveMinimum/
      else          Maximum, multipleOf, min/maxLength, min/maxItems,
                    uniqueItems, default, examples, title, deprecated,
                    readOnly/writeOnly, $schema, $defs

    Two guards keep a schema from swallowing the request: depth is capped (deep
    nesting becomes a plain object) and property/description counts are capped.
    A tool the model cannot fully describe is still callable — the server
    answers with a helpful error, which is how REA behaves anyway.
"""
from __future__ import annotations

_MAX_DEPTH = 5
_MAX_PROPERTIES = 25
_MAX_ENUM = 20
_MAX_DESC = 180

_TYPES = {"object": "OBJECT", "string": "STRING", "number": "NUMBER",
          "integer": "INTEGER", "boolean": "BOOLEAN", "array": "ARRAY"}

_KEEP_ALWAYS = ("description",)


def _infer_type(value) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "NUMBER"
    return "STRING"


def _clean_description(text, limit: int = _MAX_DESC) -> str:
    s = str(text or "").strip().replace("\n", " ")
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + "…"
    return s


def sanitize_schema(schema, defs: dict | None = None, depth: int = 0,
                    _seen: tuple = ()) -> dict:
    """One JSON Schema node → a Gemini-safe node. Never raises."""
    if not isinstance(schema, dict):
        return {"type": "STRING"}
    if depth > _MAX_DEPTH:
        return {"type": "OBJECT", "description": "Nested structure (see the tool description)."}

    # ---- $ref -------------------------------------------------------------
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref:
        name = ref.rsplit("/", 1)[-1]
        target = (defs or {}).get(name)
        if isinstance(target, dict) and name not in _seen:
            out = sanitize_schema(target, defs, depth + 1, _seen + (name,))
            if schema.get("description"):
                out["description"] = _clean_description(schema["description"])
            return out
        return {"type": "OBJECT", "description": "Structured value (see the tool description)."}

    # ---- combinators ------------------------------------------------------
    for key in ("oneOf", "anyOf"):
        branches = schema.get(key)
        if isinstance(branches, list) and branches:
            first = next((b for b in branches if isinstance(b, dict)), None)
            if first is not None:
                out = sanitize_schema(first, defs, depth, _seen)
                if schema.get("description") and "description" not in out:
                    out["description"] = _clean_description(schema["description"])
                return out

    if isinstance(schema.get("allOf"), list):
        merged = {"properties": {}, "required": []}
        for branch in schema["allOf"]:
            if not isinstance(branch, dict):
                continue
            props = branch.get("properties")
            if isinstance(props, dict):
                merged["properties"].update(props)
            req = branch.get("required")
            if isinstance(req, list):
                merged["required"].extend(r for r in req if isinstance(r, str))
        merged.update({k: v for k, v in schema.items() if k not in ("allOf",)})
        schema = merged

    # ---- const → enum -----------------------------------------------------
    # Gemini's enum accepts STRINGS ONLY, so a boolean/integer const keeps its
    # type and loses the (redundant) single-value enum instead.
    if "const" in schema and "enum" not in schema:
        const = schema["const"]
        schema = dict(schema)
        if isinstance(const, str):
            schema["enum"] = [const]
            schema["type"] = "string"
        elif isinstance(const, (int, float, bool)):
            schema["type"] = _infer_type(const)

    # ---- type (may be a list) --------------------------------------------
    raw_type = schema.get("type")
    nullable = bool(schema.get("nullable"))
    if isinstance(raw_type, list):
        names = [t for t in raw_type if isinstance(t, str)]
        if "null" in names:
            nullable = True
        raw_type = next((t for t in names if t != "null"), "string")
    gtype = _TYPES.get(str(raw_type or "").lower())

    out: dict = {}
    if gtype:
        out["type"] = gtype
    if nullable:
        out["nullable"] = True
    if schema.get("description"):
        out["description"] = _clean_description(schema["description"])

    # ---- enum (strings only — Gemini rejects numeric/boolean members) ------
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        vals = [v for v in enum if isinstance(v, str)][:_MAX_ENUM]
        if vals:
            out["enum"] = vals
            out.setdefault("type", "STRING")

    # ---- object -----------------------------------------------------------
    props = schema.get("properties")
    if isinstance(props, dict) and props:
        clean_props = {}
        for i, (name, sub) in enumerate(props.items()):
            if i >= _MAX_PROPERTIES:
                break
            if not isinstance(name, str) or not name:
                continue
            clean_props[name] = sanitize_schema(sub, defs, depth + 1, _seen)
        if clean_props:
            out["type"] = out.get("type", "OBJECT")
            out["properties"] = clean_props
            required = schema.get("required")
            if isinstance(required, list):
                keep = [r for r in required if isinstance(r, str) and r in clean_props]
                if keep:
                    out["required"] = keep

    # ---- array ------------------------------------------------------------
    if str(raw_type or "").lower() == "array" or schema.get("items") is not None:
        items = schema.get("items")
        if isinstance(items, list) and items:
            items = items[0]
        out["type"] = "ARRAY"
        out["items"] = sanitize_schema(items, defs, depth + 1, _seen)

    if not out:
        out["type"] = "STRING"          # a leaf we could not classify
    return out


def sanitize_tool_parameters(input_schema) -> tuple[dict, bool]:
    """(parameters, simplified) for one MCP tool's inputSchema."""
    if not isinstance(input_schema, dict):
        return {"type": "OBJECT", "properties": {}}, False
    defs = input_schema.get("$defs") or input_schema.get("definitions") or {}
    if not isinstance(defs, dict):
        defs = {}
    before = len(str(input_schema))
    params = sanitize_schema(input_schema, defs)
    if params.get("type") != "OBJECT":
        # a tool whose arguments are not an object cannot be declared as one
        params = {"type": "OBJECT", "properties": {},
                  "description": _clean_description(params.get("description") or "")}
    params.setdefault("properties", {})
    after = len(str(params))
    return params, after < before
