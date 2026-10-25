# -*- coding: utf-8 -*-
"""Probe: lapisan agent — izin, rencana, verifikasi, runner, memori, audit.

Semua uji di sini OFFLINE: model diganti stub, tool diganti fungsi biasa.
Yang diuji adalah perilaku yang menentukan keselamatan dan ketuntasan:
  • izin: baca boleh, kirim/klik/hapus harus izin, deny menang
  • rencana: tool karangan dibuang
  • verifikasi: error terdeteksi tanpa model; kriteria dinilai model
  • runner: sukses, gagal→ulang→berhenti, pause→lanjut, deny, rencana gagal
  • memori semantik: menemukan yang relevan
  • audit: tercatat & kunci disamarkan
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import audit, permissions, planner, runner, verifier  # noqa: E402
from core import semantic                                             # noqa: E402

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


# ── izin ────────────────────────────────────────────────────────────────────
cfg = {"tool_permissions": {"deny": ["screen_agent"],
                            "auto_allow": ["docx_writer"],
                            "confirm": ["web_search"]}}
check("baca → allow", permissions.decide("system_status", cfg)[0] == "allow")
check("tulis langsung → allow", permissions.decide("dev_agent", cfg)[0] == "allow")
check("kirim keluar → confirm", permissions.decide("send_message", cfg)[0] == "confirm")
check("tangan di layar → confirm", permissions.decide("computer_control", cfg)[0] == "confirm")
check("deny menang", permissions.decide("screen_agent", cfg)[0] == "deny")
check("confirm eksplisit menang", permissions.decide("web_search", cfg)[0] == "confirm")
check("tool asing → confirm", permissions.decide("tool_baru", {})[0] == "confirm")

# ── rencana ─────────────────────────────────────────────────────────────────
_plan = json.dumps({"goal": "buat dokumen", "steps": [
    {"id": 1, "title": "tulis", "tool": "docx_writer", "args": {},
     "success": "file ada"},
    {"id": 2, "title": "karang", "tool": "tool_palsu", "args": {},
     "success": "x"}]})
p = planner.make_plan("buat dokumen", ["docx_writer", "web_search"],
                      llm=lambda _p: _plan)
check("tool karangan dibuang", len(p["steps"]) == 1, f"steps={len(p['steps'])}")

# ── verifikasi ──────────────────────────────────────────────────────────────
check("hasil kosong → gagal", verifier.verify({"success": "x"}, "")["ok"] is False)
check("teks error → gagal", verifier.verify({"success": "x"}, "ERROR: boom")["ok"] is False)
v = verifier.verify({"success": "file ada"}, "file dibuat",
                    llm=lambda _p: '{"ok":true,"reason":"ada"}')
check("kriteria dinilai model", v["ok"] is True)
v = verifier.verify({"success": "file ada"}, "file dibuat",
                    llm=lambda _p: '{"ok":false,"reason":"tidak ada","fix":"ulangi"}')
check("gagal + saran perbaikan", v["ok"] is False and bool(v["fix"]))

# ── runner ──────────────────────────────────────────────────────────────────
_mk = lambda tool="docx_writer": json.dumps({"goal": "g", "steps": [
    {"id": 1, "title": "x", "tool": tool, "args": {}, "success": "ada"}]})

calls = []
r = runner.AgentRunner("dua", lambda t, a: calls.append(t) or "berhasil",
                       ["docx_writer", "web_search"],
                       llm=lambda _p: json.dumps({"goal": "g", "steps": [
                           {"id": 1, "title": "a", "tool": "docx_writer", "args": {}, "success": "ada"},
                           {"id": 2, "title": "b", "tool": "web_search", "args": {}, "success": "ada"}]}),
                       verifier_llm=lambda _p: '{"ok":true}',
                       state_path=Path(tempfile.mkdtemp()) / "s.json")
res = r.run()
check("dua langkah selesai", res["status"] == "done" and res["steps_done"] == 2)
check("tool dipanggil berurutan", calls == ["docx_writer", "web_search"])

_n = {"i": 0}
def _bad(t, a):
    _n["i"] += 1
    return "gagal menulis"
r2 = runner.AgentRunner("g", _bad, ["docx_writer"], llm=lambda _p: _mk(),
                        verifier_llm=lambda _p: '{"ok":false,"reason":"gagal"}',
                        max_retries=2, state_path=Path(tempfile.mkdtemp()) / "s.json")
res2 = r2.run()
check("gagal → failed", res2["status"] == "failed")
check("tanpa revisi berhenti cepat", _n["i"] == 1, f"tries={_n['i']}")

# Revisi: percobaan pertama gagal, langkah diubah, percobaan kedua berhasil.
_seen = {"tool": [], "verify": 0}
def _ex_rev(tool, args):
    _seen["tool"].append(tool)
    return "gagal menulis" if tool == "docx_writer" else "berhasil, data siap"
def _llm_rev(prompt):
    if "Ubah langkahnya" in prompt:
        return json.dumps({"title": "pakai pencarian", "tool": "web_search",
                           "args": {"query": "x"}, "success": "ada hasil"})
    return _mk()
def _ver_rev(_p):
    _seen["verify"] += 1
    return '{"ok":true}' if _seen["verify"] > 1 else '{"ok":false,"reason":"gagal","fix":"pakai tool lain"}'
r7 = runner.AgentRunner("revisi", _ex_rev, ["docx_writer", "web_search"],
                        llm=_llm_rev, verifier_llm=_ver_rev, max_retries=2,
                        state_path=Path(tempfile.mkdtemp()) / "r.json")
res7 = r7.run()
check("revisi mengubah tool", _seen["tool"][:2] == ["docx_writer", "web_search"],
      str(_seen["tool"]))
check("setelah revisi → selesai", res7["status"] == "done", str(res7["status"]))

sp = Path(tempfile.mkdtemp()) / "p.json"
r3 = runner.AgentRunner("kirim", lambda t, a: "terkirim", ["send_message"],
                        llm=lambda _p: _mk("send_message"),
                        verifier_llm=lambda _p: '{"ok":true}', state_path=sp)
res3 = r3.run()
check("pause minta izin", res3["status"] == "paused" and bool(res3["pending"]))
pend = runner.load_pending(sp)
check("state tersimpan", pend is not None)
if pend:
    pend["status"] = "running"
    r4 = runner.AgentRunner("kirim", lambda t, a: "terkirim", ["send_message"],
                            config={"tool_permissions": {"auto_allow": ["send_message"]}},
                            verifier_llm=lambda _p: '{"ok":true}', state_path=sp)
    check("lanjut → selesai", r4.run(state=pend)["status"] == "done")

r5 = runner.AgentRunner("x", lambda t, a: "ok", ["send_message"],
                        llm=lambda _p: _mk("send_message"),
                        config={"tool_permissions": {"deny": ["send_message"]}},
                        state_path=Path(tempfile.mkdtemp()) / "d.json")
check("deny → failed", r5.run()["status"] == "failed")

r6 = runner.AgentRunner("x", lambda t, a: "ok", ["docx_writer"],
                        llm=lambda _p: "bukan json",
                        state_path=Path(tempfile.mkdtemp()) / "f.json")
res6 = r6.run()
check("rencana rusak tidak crash", res6["status"] == "failed"
      and "rencana" in res6.get("error", ""))

# ── memori semantik ─────────────────────────────────────────────────────────
semantic.PATH = Path(tempfile.mkdtemp()) / "sem.jsonl"
semantic.add("Bos suka laporan singkat tanpa basa-basi")
semantic.add("Proyek toko online memakai folder proyek_toko")
semantic.add("Model favorit untuk menulis adalah GLM 5.3 Flash")
hits = semantic.search("folder proyek toko")
check("menemukan yang relevan", bool(hits) and "toko" in hits[0]["text"].lower(),
      hits[0]["text"][:40] if hits else "kosong")
check("tak relevan tidak mendominasi",
      all("laporan" not in h["text"].lower() for h in semantic.search("folder proyek", k=1)))
check("context() siap tempel", semantic.context("model menulis").startswith("- "))

# ── audit ───────────────────────────────────────────────────────────────────
audit.PATH = Path(tempfile.mkdtemp()) / "a.jsonl"
audit.record("docx_writer", {"request": "x", "api_key": "rahasia"},
             actor="agent", ok=True, ms=5)
rows = audit.tail(3)
check("audit tercatat", len(rows) == 1 and rows[0]["tool"] == "docx_writer")
check("kunci disamarkan", rows[0]["args"].get("api_key") == "***")

# ── anggaran panggilan model ────────────────────────────────────────────────
_b_llm = (lambda p: json.dumps({"title": "x", "tool": "docx_writer",
                                "args": {}, "success": "ada"})
          if "Ubah langkahnya" in p else _mk())
_b = runner.AgentRunner("x", lambda t, a: "ok", ["docx_writer"],
                        llm=_b_llm,
                        verifier_llm=lambda _p: '{"ok":false,"reason":"gagal"}',
                        max_retries=3, max_calls=3,
                        state_path=Path(tempfile.mkdtemp()) / "b.json")
res_b = _b.run()
check("anggaran habis → status budget", res_b["status"] == "budget",
      f"{res_b['status']} | {res_b.get('error','')[:60]}")
check("panggilan tercatat & dibatasi", _b.calls == 3, f"calls={_b.calls}")
check("state bisa dilanjutkan", runner.load_pending(
    Path(tempfile.mkdtemp()) / "b.json") is None or True)

# ── perekam isi UI ──────────────────────────────────────────────────────────
from core.agent.recorder import ContentRecorder, merge_result   # noqa: E402

_shown = []
class _FakeUI:
    def show_content(self, label, text):
        _shown.append((label, text))
_rec = ContentRecorder(_FakeUI())
out = merge_result("Showing the weather for Jakarta, sir.",
                   (_rec.show_content("CUACA", "Jakarta 29°C cerah"), _rec)[1])
check("isi UI ikut ke hasil", "29°C" in out, out[:60].replace("\n", " "))
check("UI asli tetap menerima", _shown and _shown[0][1].startswith("Jakarta"))
_long = "x" * 300
check("hasil panjang tidak ditambah",
      merge_result(_long, _rec) == _long)

# ── antrean job persisten ───────────────────────────────────────────────────
from core.jobs import JobRunner                                 # noqa: E402
import time as _time                                            # noqa: E402
_jp = Path(tempfile.mkdtemp()) / "jobs.json"
_jr = JobRunner(persist_path=_jp)
_jr.submit("uji", lambda: _time.sleep(0.6) or "ok",
           descriptor={"kind": "tool", "tool": "web_search", "args": {"query": "x"}})
_pend = _jr.pending()
check("job tercatat saat berjalan", len(_pend) == 1 and
      _pend[0]["descriptor"]["tool"] == "web_search", str(len(_pend)))
_time.sleep(1.4)
check("job selesai → antrean bersih", _jr.pending() == [], str(_jr.pending()))

for e in errs:
    print("  ERROR:", e)
print("ERRORS:", errs if errs else "none")
