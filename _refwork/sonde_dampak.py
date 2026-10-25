# -*- coding: utf-8 -*-
"""Probe audit: apakah lapisan agent punya dampak yang tidak diinginkan?

Bukan menguji fitur baru (itu sonde_agent), tapi menguji DAMPAKNYA:
  1. Setiap tool bawaan punya keputusan izin yang masuk akal — tidak ada
     tool aman yang tiba-tiba selalu minta izin, dan tidak ada tool berbahaya
     yang lolos tanpa izin.
  2. Perekam `player` tidak merusak plugin yang memakai atribut UI lain.
  3. `JobRunner.submit` tetap kompatibel dengan pemanggil lama (tanpa
     descriptor) dan antrean tidak menulis berkas saat tidak diminta.
  4. Sinyal anggaran (BaseException) tidak lolos dari AgentRunner.run().
  5. Memori semantik tidak mencampur entri saat berkas kosong/rusak.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import permissions, runner                    # noqa: E402
from core.agent.recorder import ContentRecorder, merge_result  # noqa: E402
from core.jobs import JobRunner                                # noqa: E402
from core import semantic                                      # noqa: E402

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


# ── 1. keputusan izin untuk SETIAP tool bawaan ──────────────────────────────
import main                                                     # noqa: E402

cfg = json.loads((Path("config") / "api_keys.json").read_text(encoding="utf-8"))
builtins = [d["name"] for d in main.TOOL_DECLARATIONS]
_allow_expected = {
    "save_memory", "agent_run", "reload_plugins", "switch_model",
    "screen_share", "screen_process", "model_status", "system_status",
    "recall_memory", "close_camera", "manage_monitor", "brain",
    "custom_brain",
}
_confirm_expected = {"send_message", "shutdown_jarvis", "undo"}
bad = []
for t in builtins:
    d, _why = permissions.decide(t, cfg)
    if t in _allow_expected and d != "allow":
        bad.append(f"{t}={d}")
    if t in _confirm_expected and d != "confirm":
        bad.append(f"{t}={d}")
check("tool bawaan punya izin yang benar", not bad, str(bad) or f"{len(builtins)} tool")

# tool berbahaya TIDAK boleh lolos
for t in ("shutdown_jarvis", "send_message", "screen_agent", "computer_control"):
    check(f"  {t} tetap minta izin", permissions.decide(t, cfg)[0] == "confirm")
# composio_actions: gerbang konfirmasinya ada DI DALAM plugin (kalimat
# spesifik + token argumen identik, TTL 3 menit, jejak audit sendiri).
# Layer izin sengaja meloloskannya — meminta konfirmasi dua kali untuk satu
# email adalah keluhan bos. Sinyal bahwa ia tetap terlindung: deskripsi tool
# dan gate plugin (sonde_uithread + sonde_composio_exec).
_d, _why = permissions.decide("composio_actions", cfg)
check("composio_actions tanpa konfirmasi ganda", _d == "allow",
      f"{_d} ({_why})")

# ── 2. perekam tidak merusak akses atribut UI lain ──────────────────────────
class _FakeUI:
    def __init__(self):
        self.current_file = "C:/data/x.pdf"
        self.muted = False
    def show_content(self, label, text):
        self.seen = (label, text)

ui = _FakeUI()
rec = ContentRecorder(ui)
check("atribut UI diteruskan", rec.current_file == "C:/data/x.pdf")
check("atribut UI (2)", rec.muted is False)
rec.show_content("CUACA", "Jakarta 29C")
check("show_content diteruskan", ui.seen[1].startswith("Jakarta"))
check("perekam kosong → hasil apa adanya",
      merge_result("ok", ContentRecorder(None)) == "ok")

# ── 3. JobRunner kompatibel & tidak menulis berkas tanpa path ───────────────
import time as _t                                                # noqa: E402
_jr = JobRunner()                      # tanpa persist_path = perilaku lama
_jid = _jr.submit("lama", lambda: "ok")
check("submit lama tetap jalan", _jid.startswith("job"))
_t.sleep(0.8)
check("tanpa path → tidak ada berkas",
      _jr.pending() == [] and _jr.status()[0]["status"] == "done")

_jp = Path(tempfile.mkdtemp()) / "j.json"
_jr2 = JobRunner(persist_path=_jp)
_jr2.submit("baru", lambda: _t.sleep(0.4) or "ok", descriptor={"tool": "web_search"})
check("dengan path → tercatat", len(_jr2.pending()) == 1)

# nama tool ikut tercatat di Job (dibaca _on_job_done untuk membedakan
# kalimat gagal composio dari sukses)
_jr3 = JobRunner()
_jid3 = _jr3.submit("composio", lambda: "ok",
                    descriptor={"kind": "tool", "tool": "composio_actions",
                                "args": {}})
_t.sleep(0.6)
check("Job membawa nama tool", _jr3._jobs[_jid3].tool == "composio_actions",
      _jr3._jobs[_jid3].tool)

# ── 4. anggaran tidak lolos dari run() ──────────────────────────────────────
def _llm(p):
    return json.dumps({"title": "x", "tool": "web_search", "args": {},
                       "success": "ada"}) if "Ubah langkahnya" in p else \
        json.dumps({"goal": "g", "steps": [{"id": 1, "title": "x",
                                           "tool": "web_search", "args": {},
                                           "success": "ada"}]})
r = runner.AgentRunner("x", lambda t, a: "ok", ["web_search"], llm=_llm,
                       verifier_llm=lambda _p: '{"ok":false,"reason":"x"}',
                       max_retries=4, max_calls=3,
                       state_path=Path(tempfile.mkdtemp()) / "s.json")
try:
    res = r.run()
    check("anggaran ditangani, bukan dilempar", res["status"] == "budget",
          res["status"])
except BaseException as e:                                   # noqa: BLE001
    check("anggaran ditangani, bukan dilempar", False, f"{type(e).__name__}")

# ── 5. memori semantik tahan berkas rusak ──────────────────────────────────
_p = Path(tempfile.mkdtemp()) / "sem.jsonl"
_p.write_text("bukan json\n{rusak\n", encoding="utf-8")
semantic.PATH = _p
check("berkas rusak → tidak crash", semantic.search("apa saja") == [])
semantic.add("ingatan baru setelah rusak")
check("bisa menulis setelah rusak", len(semantic.search("ingatan baru")) == 1)

# ── 6. aksi panjang dijalankan di latar ─────────────────────────────────────
class _Stub:
    _should_background = main.JarvisLive._should_background

s = _Stub()
check("composio kirim → latar",
      s._should_background("composio_actions", {"action": "gmail_send_email"}))
check("composio status → langsung",
      not s._should_background("composio_actions", {"action": "status"}))
check("composio list → langsung",
      not s._should_background("composio_actions", {"action": "calendar_list"}))
check("dev_agent → latar", s._should_background("dev_agent", {"request": "x"}))
check("web_search → langsung", not s._should_background("web_search", {"query": "x"}))
check("screen_agent → langsung",
      not s._should_background("screen_agent", {"goal": "x"}))
check("send_message → latar", s._should_background("send_message", {}))
src = Path("main.py").read_text(encoding="utf-8")
check("baris kemajuan untuk tool inline",
      'task_progress(f"Menjalankan {name}…")' in src)
check("baris kemajuan dihormati otak kustom", "if not self._in_brain:" in src)

for e in errs:
    print("  ERROR:", e)
print("ERRORS:", errs if errs else "none")
