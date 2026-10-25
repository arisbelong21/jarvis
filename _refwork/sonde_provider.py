"""Sonde providers/llm_openai.py — terutama respons SSE yang membuat
giliran otak mati ("provider returned non-JSON"), plus jalur JSON biasa.
requests.post distub; tidak ada jaringan.
"""
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT, ERRORS = [], []


def check(name, cond, detail=""):
    line = f"  [{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else "")
    REPORT.append(line)
    if not cond:
        ERRORS.append(name)


spec = importlib.util.spec_from_file_location(
    "llm_openai", os.path.join(ROOT, "providers", "llm_openai.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

import requests                      # noqa: E402  (dipakai bersama stub)


class FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def json(self):
        return json.loads(self.text)


def stub(text, status=200, capture=None):
    def post(url, headers=None, json=None, timeout=None):
        if capture is not None:
            capture["url"] = url
            capture["body"] = json
        return FakeResp(text, status)
    return post


LLM = mod.OpenAILLM("test", {"base_url": "https://x.ai/v1",
                             "api_key": "k", "model": "m"})

# ── 1. JSON biasa — perilaku tak berubah ─────────────────────────────────────
cap = {}
requests.post = stub(json.dumps({"choices": [{"message": {
    "role": "assistant", "content": "halo bro"}}]}), capture=cap)
text, calls = LLM.complete([{"role": "user", "content": "hai"}])
check("JSON biasa: teks terbaca", text == "halo bro", text)
check("body meminta stream=False", cap["body"].get("stream") is False,
      str(cap["body"].get("stream")))

# ── 2. SSE konten saja ──────────────────────────────────────────────────────
sse = "\n".join([
    'data: {"choices":[{"delta":{"role":"assistant","content":"Halo "}}]}',
    'data: {"choices":[{"delta":{"content":"raja"}}]}',
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
    'data: [DONE]',
])
requests.post = stub(sse)
text, calls = LLM.complete([{"role": "user", "content": "hai"}])
check("SSE: konten dirangkai", text == "Halo raja", text)
check("SSE: tanpa tool call", calls == [])

# ── 3. SSE dengan tool call terpotong-potong ────────────────────────────────
sse_tools = "\n".join([
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
    '"function":{"name":"gmail_","arguments":""}}]}}]}',
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
    '"function":{"name":"send","arguments":"{\\"to\\":"}}]}}]}',
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
    '"function":{"arguments":"\\"budi@x.co\\"}"}}]}}]}',
    'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
    'data: [DONE]',
])
requests.post = stub(sse_tools)
text, calls = LLM.complete([{"role": "user", "content": "kirim"}],
                           tools=[{"type": "function", "function": {
                               "name": "gmail_send", "parameters": {}}}])
check("SSE: satu tool call tersusun", len(calls) == 1, str(len(calls)))
if calls:
    check("SSE: nama tool utuh (tak dobel)",
          calls[0].name == "gmail_send", calls[0].name)
    check("SSE: id call dipertahankan", calls[0].id == "call_1", calls[0].id)
    check("SSE: arguments JSON dirangkai",
          calls[0].args == {"to": "budi@x.co"}, str(calls[0].args))

# ── 4. SSE dengan sampah di tengah ───────────────────────────────────────────
sse_noisy = "\n".join([
    ": keep-alive",
    "",
    'data: {"choices":[{"delta":{"content":"oke"}}]}',
    'data: BUKAN-JSON',
    'data: {"choices":[{"delta":{"content":" deh"}}]}',
    'data: [DONE]',
])
requests.post = stub(sse_noisy)
text, calls = LLM.complete([{"role": "user", "content": "x"}])
check("SSE: baris non-JSON dilewati", text == "oke deh", text)

# ── 5. DUA tool call paralel dalam satu stream ──────────────────────────────
sse_two = "\n".join([
    'data: {"choices":[{"delta":{"tool_calls":['
    '{"index":0,"id":"c0","function":{"name":"web_search","arguments":"{\\"query\\":\\"a\\"}"}},'
    '{"index":1,"id":"c1","function":{"name":"recall_memory","arguments":"{}"}}]}}]}',
    'data: [DONE]',
])
requests.post = stub(sse_two)
text, calls = LLM.complete([{"role": "user", "content": "x"}],
                           tools=[{"type": "function", "function": {
                               "name": "web_search", "parameters": {}}}])
check("SSE: dua tool call terpisah",
      [c.name for c in calls] == ["web_search", "recall_memory"],
      str([c.name for c in calls]))

# ── 6. SSE dengan nama tool DIULANG tiap chunk ──────────────────────────────
sse_repeat = "\n".join([
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c9",'
    '"function":{"name":"web_search","arguments":"{\\"q\\":"}}]}}]}',
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
    '"function":{"name":"web_search","arguments":"\\"a\\"}"}}]}}]}',
    'data: [DONE]',
])
requests.post = stub(sse_repeat)
text, calls = LLM.complete([{"role": "user", "content": "x"}],
                           tools=[{"type": "function", "function": {
                               "name": "web_search", "parameters": {}}}])
check("SSE: nama diulang-chunk tak dobel",
      len(calls) == 1 and calls[0].name == "web_search"
      and calls[0].args == {"q": "a"},
      f"{[c.name for c in calls]} {[c.args for c in calls]}")

# ── 7. HTTP error tetap dilaporkas jujur ────────────────────────────────────
requests.post = stub(json.dumps({"error": {"message": "Invalid key"}}), 401)
try:
    LLM.complete([{"role": "user", "content": "x"}])
    check("HTTP 401 → RuntimeError", False, "tak ada exception")
except RuntimeError as e:
    check("HTTP 401 → RuntimeError", "401" in str(e), str(e)[:60])

print("\n".join(REPORT))
print(f"\nERRORS: {'none' if not ERRORS else ', '.join(ERRORS)}")
sys.exit(1 if ERRORS else 0)
