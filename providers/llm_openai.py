"""
OpenAI-compatible provider adapter.

WHAT IT SPEAKS
    POST {base_url}/chat/completions — the dialect OpenAI defined and the
    aggregators and local runtimes all copied. One base_url + api_key +
    model name is the whole configuration; requests is the only
    dependency, and it is imported inside the call so nothing here is
    paid for until a provider rung is actually used.

WHY ERRORS LOOK THE WAY THEY DO
    Every failure carries the HTTP status in its message ("HTTP 429:
    ..."), because core/gemini.py's ladder classifies rungs by status —
    quota (429) rests five minutes, gone (404/401/403) six hours,
    unavailable (503/504) thirty — and a provider rung must sit under
    exactly the same rules as a Gemini name. Connection failures raise
    without a status, so they move the ladder on without resting the
    rung, which is right: a dropped network is not the model's fault.
"""

from __future__ import annotations

import json


class _Call:
    """One tool call from the model, in the shape callers expect."""

    __slots__ = ("id", "name", "args")

    def __init__(self, cid: str, name: str, args: dict):
        self.id, self.name, self.args = cid, name, args


def _parse_sse(raw: str) -> dict:
    """Rebuild one chat.completion out of an SSE stream body.

    Providers that stream send `data: {chunk}` lines: content arrives in
    deltas, and one tool call arrives in pieces — name first, then its
    arguments JSON in slices, indexed by `index`. Stitched back together
    here so the caller sees exactly what a non-streaming reply would have
    been. A [DONE] marker, blank lines and unparseable chunks are skipped.
    """
    content = ""
    slots: dict[int, dict] = {}
    finish = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            if payload == "[DONE]":
                break
            continue
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        for ch in chunk.get("choices") or []:
            delta = ch.get("delta") or {}
            if delta.get("content"):
                content += delta["content"]
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = slots.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    # Some providers stream the name in pieces, others repeat
                    # the whole name in every chunk — both are handled by
                    # appending here and collapsing an exact doubling below.
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    msg = {"role": "assistant", "content": content}
    calls = []
    for idx in sorted(slots):
        slot = slots[idx]
        name = slot["name"]
        if name and len(name) % 2 == 0 \
                and name[:len(name) // 2] == name[len(name) // 2:]:
            name = name[:len(name) // 2]   # repeated per chunk, not split
        if name:
            calls.append({
                "id": slot["id"] or f"call_{idx}",
                "type": "function",
                "function": {"name": name,
                             "arguments": slot["arguments"] or "{}"},
            })
    if calls:
        msg["tool_calls"] = calls
    return {"choices": [{"index": 0, "message": msg,
                         "finish_reason": finish}]}


class OpenAILLM:
    """One configured provider: where it lives, which model, its key."""

    def __init__(self, pid: str, config: dict):
        self.pid = pid
        self.base_url = str(config.get("base_url") or "").rstrip("/")
        self.api_key = str(config.get("api_key") or "")
        self.model = str(config.get("model") or "")
        if not self.base_url or not self.model:
            raise RuntimeError(
                f"provider '{pid}' needs base_url and model in its "
                f"configuration (config/api_keys.json -> providers)")

    def complete(self, messages, tools=None, timeout: float = 120):
        """
        One chat completion. Returns (text, calls): the reply text — may
        be empty when the model only calls tools — and a list of _Call.

        `messages` follows the OpenAI shape: a system message plus a
        user message whose content is a string, or a list of text and
        image_url parts. `tools` is the output of providers/schema, or
        None for plain generation (which is what the desktop skills
        use — tool calling through a provider is the cloud brain's job).
        """
        import requests

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
                timeout=(10, max(10, int(timeout))),
            )
        except requests.RequestException as e:
            raise RuntimeError(f"provider connection failed: {e}") from e
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        raw = resp.text
        try:
            data = resp.json()
        except ValueError:
            # Some aggregators stream (SSE) even when the body says otherwise,
            # and the stream may open with comment lines. Reading that as JSON
            # kills the whole turn with "non-JSON" — the user hears silence.
            # Rebuild the single message it was streamed as instead.
            data = _parse_sse(raw)
            if not data.get("choices"):
                raise RuntimeError(
                    f"provider returned non-JSON: {raw[:200]}") from None

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("provider returned no choices")
        msg = choices[0].get("message") or {}
        text = str(msg.get("content") or "").strip()

        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            calls.append(_Call(
                str(tc.get("id") or f"call_{i}"),
                str(fn.get("name") or ""),
                args,
            ))
        return text, calls
