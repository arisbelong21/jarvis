"""Sonde core/custom_brain.py — offline (stub LLM) + satu putaran nyata.

Offline: agent loop, eksekutor tool, sejarah, plafon langkah, error provider.
Live (ditandai): satu giliran sungguhan ke provider aktif (GLM).
"""
import asyncio
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
REPORT, ERRORS = [], []


def check(name, cond, detail=""):
    line = f"  [{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else "")
    REPORT.append(line)
    if not cond:
        ERRORS.append(name)


spec = importlib.util.spec_from_file_location(
    "custom_brain", os.path.join(ROOT, "core", "custom_brain.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class _Call:
    def __init__(self, cid, name, args):
        self.id, self.name, self.args = cid, name, args


class StubLLM:
    """Scripted turns: each entry is (text, [calls]) or the string 'RAISE'."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def complete(self, messages, tools=None, timeout=120):
        self.seen.append([dict(m) for m in messages])
        item = self.script.pop(0) if self.script else ("(habis)", [])
        if item == "RAISE":
            raise RuntimeError("provider mati")
        return item


def run(coro):
    return asyncio.run(coro)


# ── 1. satu giliran polos: teks langsung, tanpa tool ─────────────────────────
llm = StubLLM([("Halo Bos, ada yang bisa saya bantu?", [])])
brain = mod.CustomBrain(llm, "SYSTEM")
calls_seen = []


async def _exec(name, args):
    calls_seen.append((name, args))
    return f"hasil {name}"


said = run(brain.turn("hai", [], _exec))
check("teks polos diteruskan apa adanya", said.startswith("Halo Bos"), said[:40])
check("tanpa tool → eksekutor tak dipanggil", calls_seen == [])

# ── 2. satu giliran bertool: panggil → eksekusi → jawab akhir ────────────────
llm2 = StubLLM([
    ("Sebentar saya cek.", [_Call("c1", "web_search", {"query": "cuaca Jakarta"})]),
    ("Cuaca Jakarta hari ini cerah, 31 derajat.", []),
])
brain2 = mod.CustomBrain(llm2, "SYSTEM")
calls2 = []


async def _exec2(name, args):
    calls2.append((name, args))
    return "cuaca: cerah 31C"


said2 = run(brain2.turn("cuaca jakarta hari ini?", [], _exec2))
check("tool dipanggil lewat eksekutor", calls2 == [("web_search", {"query": "cuaca Jakarta"})],
      str(calls2))
check("jawaban akhir setelah tool", said2.startswith("Cuaca Jakarta"), said2[:40])
tool_msgs = [m for m in llm2.seen[-1] if m.get("role") == "tool"]
check("hasil tool masuk ke pesan LLM",
      tool_msgs and tool_msgs[0]["content"] == "cuaca: cerah 31C"
      and tool_msgs[0]["tool_call_id"] == "c1", str(tool_msgs)[:80])
check("sejarah simpan user+assistant",
      brain2._hist[-2]["content"] == "cuaca jakarta hari ini?"
      and brain2._hist[-1]["content"].startswith("Cuaca Jakarta"))

# ── 3. plafon langkah: LLM terus memanggil tool ──────────────────────────────
llm3 = StubLLM([("", [_Call(f"c{i}", "recall_memory", {})]) for i in range(20)])
brain3 = mod.CustomBrain(llm3, "SYSTEM")
n = {"i": 0}


async def _exec3(name, args):
    n["i"] += 1
    return "ok"


said3 = run(brain3.turn("lingkaran", [], _exec3))
# 22 Okt — kontrak baru: panggilan IDENTIK di-cache (G2), jadi eksekutor
# dipanggil sekali; plafon langkah tetap 8 dan pesannya jujur.
check("panggilan identik dieksekusi sekali (cache)", n["i"] == 1, f"{n['i']} panggilan")
check("plafon 8 langkah", brain3.max_steps == 8, brain3.max_steps)
check("pesan plafon jujur", ("BELUM selesai" in said3
                             and "langkah" in said3), said3[:80])

# 3b. argumen BERBEDA tiap langkah -> dedupe tidak berlaku, plafon dihormati
llm3b = StubLLM([("", [_Call(f"d{i}", "recall_memory", {"i": i})])
                 for i in range(20)])
brain3b = mod.CustomBrain(llm3b, "SYSTEM")
m = {"i": 0}


async def _exec3b(name, args):
    m["i"] += 1
    return "ok"


said3b = run(brain3b.turn("lingkaran", [], _exec3b))
check("plafon dihormati (args berbeda)", m["i"] == brain3b.max_steps,
      f"{m['i']} panggilan")
check("pesan plafon jujur (args berbeda)",
      "BELUM selesai" in said3b and "langkah" in said3b, said3b[:80])

# ── 4. provider mati → kalimat jujur, bukan traceback ────────────────────────
llm4 = StubLLM(["RAISE"])
brain4 = mod.CustomBrain(llm4, "SYSTEM")
said4 = run(brain4.turn("apa kabar", [], _exec))
check("provider gagal → kalimat Indonesia",
      "tidak bisa dihubungi" in said4 and "provider mati" in said4, said4[:70])

# ── 5. eksekutor melempar → jadi pesan tool, loop lanjut ─────────────────────
llm5 = StubLLM([
    ("", [_Call("c9", "tool_gila", {"x": 1})]),
    ("Baik, tool-nya gagal.", []),
])
brain5 = mod.CustomBrain(llm5, "SYSTEM")


async def _exec5(name, args):
    raise ValueError("meletus")


said5 = run(brain5.turn("coba", [], _exec5))
err_msgs = [m for m in llm5.seen[-1] if m.get("role") == "tool"]
check("error tool jadi pesan tool, bukan crash",
      err_msgs and "meletus" in err_msgs[0]["content"], str(err_msgs)[:80])
check("loop tetap sampai jawaban akhir", said5.startswith("Baik"), said5[:40])

# ── 6. sejarah dipangkas ──────────────────────────────────────────────────────
llm6 = StubLLM([("oke", []) for _ in range(30)])
brain6 = mod.CustomBrain(llm6, "SYSTEM")
for i in range(15):
    run(brain6.turn(f"pesan {i}", [], _exec))
check("sejarah dibatasi", len(brain6._hist) <= 2 * mod.CustomBrain.HISTORY,
      f"{len(brain6._hist)} pesan")

# ── 6b. register_tools: temuan search_tools ikut ke request berikut ─────────
from providers.schema import to_openai as _to_oa   # noqa: E402
llm6b = StubLLM([("oke", [])])
brain6b = mod.CustomBrain(llm6b, "S")
dummy = _to_oa([{"name": "mcp_rea_decompile", "description": "x",
                 "parameters": {"type": "OBJECT", "properties": {}}}])
brain6b.register_tools(dummy)
run(brain6b.turn("cek", [], _exec))
check("register_tools ada & reset bersih",
      hasattr(brain6b, "register_tools") and hasattr(brain6b, "reset"))

# stub yang merekam argumen tools tiap complete
class SpyLLM(StubLLM):
    def __init__(self):
        super().__init__([("ok", [])])
        self.tools_seen = []

    def complete(self, messages, tools=None, timeout=120):
        self.tools_seen.append(list(tools or []))
        return super().complete(messages, tools, timeout)


spy = SpyLLM()
b7 = mod.CustomBrain(spy, "S")
b7.register_tools(dummy)
run(b7.turn("hai", [{"type": "function", "function": {"name": "web_search",
                                                     "parameters": {}}}],
            _exec))
first_tools = [t["function"]["name"] for t in spy.tools_seen[0]]
check("tool temuan dikirim pada request",
      "mcp_rea_decompile" in first_tools and "web_search" in first_tools,
      str(first_tools))
b7.reset()
run(b7.turn("hai lagi", [{"type": "function", "function": {"name": "web_search",
                                                           "parameters": {}}}],
            _exec))
after = [t["function"]["name"] for t in spy.tools_seen[1]]
check("reset() membersihkan tool temuan", "mcp_rea_decompile" not in after,
      str(after))

# ── 7. LIVE: satu putaran sungguhan ke provider aktif ────────────────────────
def live_check():
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import json
        cfg = json.load(open(os.path.join(ROOT, "config", "api_keys.json"),
                             encoding="utf-8"))
        if str(cfg.get("voice_brain") or "").lower() != "custom":
            check("LIVE: dilewati (voice_brain bukan 'custom')", True)
            return
        from memory.config_manager import (get_active_provider,
                                           get_provider_config,
                                           list_provider_ids)
        from providers import registry
        from providers.schema import to_openai
        # 23 Okt — pakai FallbackLLM seperti aplikasi: satu provider kena
        # kuota (429) tidak boleh membuat sonde ini gagal palsu.
        _active = get_active_provider() or (list_provider_ids()[0]
                                            if list_provider_ids() else "")
        _order = ([_active] if _active else []) + [
            p for p in list_provider_ids() if p != _active]
        _provs = [(p, registry.build(p, get_provider_config(p)))
                  for p in _order]
        llm = mod.FallbackLLM(_provs)
        b = mod.CustomBrain(llm, "Jawab singkat dalam bahasa Indonesia.")
        said = run(b.turn("Sebutkan 2+2. Jawab singkat saja.", [], _exec))
        check("LIVE: provider aktif menjawab", "4" in said, said[:80])

        # putaran bertool sungguhan: LLM asli memanggil tool, hasil masuk,
        # jawaban akhir disusun dari hasil tool.
        decls = [{"name": "web_search", "description": "Cari berita di web.",
                  "parameters": {"type": "OBJECT",
                                 "properties": {"query": {"type": "STRING"}},
                                 "required": ["query"]}}]
        hits = []

        async def _exec_news(name, args):
            hits.append((name, args))
            return "Berita: Indonesia menang 2-0 atas Malaysia tadi malam."

        said2 = run(b.turn("Cari berita bola Indonesia terbaru",
                           to_openai(decls), _exec_news))
        check("LIVE: loop tool (panggil → hasil → jawab)",
              bool(hits) and "menang" in said2.lower(),
              f"tool={hits} jawab={said2[:60]}")
    except Exception as e:                       # noqa: BLE001
        check("LIVE: provider aktif menjawab", False, f"{type(e).__name__}: {e}")


live_check()

print("\n".join(REPORT))
print(f"\nERRORS: {'none' if not ERRORS else ', '.join(ERRORS)}")
sys.exit(1 if ERRORS else 0)
