# -*- coding: utf-8 -*-
"""Probe: keselamatan thread UI + pelaporan jujur hasil pekerjaan latar.

LATAR (laporan bos):
  Setelah sesi Gemini melakukan reconnect (GoAway → 1008), Jarvis crash
  exit -1073741819 (access violation) di ui.py mainloop, didahului spam
  "QObject::setParent: Cannot set parent, new parent is in a different
  thread". Ditambah: email kadang gagal terkirim tapi Jarvis mengumumkan
  "sudah selesai" dan pengguna harus bertanya apakah berhasil.

AKAR:
  1. Aplikasi menjalankan Qt di MAIN THREAD, JarvisLive.run() di THREAD
     ASYNCIO, callback sounddevice di THREAD KETIGA. Beberapa jalur UI
     menyentuh widget langsung dari thread asyncio — paling sering
     set_services(), yang dipanggil SETIAP reconnect. Menyentuh widget dari
     dua thread membocorkan state internal Qt dan berakhir sebagai access
     violation di QApplication.exec() — crash native tanpa traceback.
  2. composio_actions melaporkan kegagalan sebagai KALIMAT, bukan exception
     → job berstatus "done" → _on_job_done mengumumkan "sudah selesai"
     walau email tidak terkirim.

Sonde ini mengunci perbaikan:
  1. Facade JarvisUI tidak lagi memanggil metode widget langsung untuk jalur
     lintas-thread (set_services / refresh_model_ui / notify_phone_connected /
     mute) — semuanya lewat sinyal Qt (auto-queued ke thread GUI).
  2. Sinyal baru benar-benar dideklarasikan, disambungkan, dan punya slot.
  3. _job_verdict membedakan GAGAL / menunggu konfirmasi / selesai — status
     job saja tidak dipercaya.
  4. looks_not_done mengenali kalimat gagal ASLI plugin dan tidak
     menyalahkan kalimat sukses maupun kalimat konfirmasi.
  5. Job membawa nama tool-nya (dari descriptor) dan composio_actions tidak
     lagi dikonfirmasi dua kali (layer izin meloloskan; gate plugin yang
     memegang, lengkap dengan jejak audit).
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import permissions                       # noqa: E402
from core.jobs import JobRunner                          # noqa: E402

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI_SRC = (ROOT / "ui.py").read_text(encoding="utf-8")
MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")

# ── 1. pemanggilan widget LANGSUNG dari thread non-Qt harus HILANG ──────────
#
# Semua string di bawah ini dulu ada di facade JarvisUI dan bisa dipanggil
# dari thread asyncio/audio. Kalau salah satu muncul lagi, probe gagal.
_BANNED = (
    "self._win.set_services(",          # facade: papan status + kartu TOOLS
    "self._win._refresh_model_picker(", # facade: chip model + kartu models
    "self._win._refresh_models_card(",
    "self._win.notify_phone_connected(",
    "self._win._toggle_mute(",          # facade: bisu/bunyi
)
for s in _BANNED:
    check(f"lintas-thread langsung hilang: {s}", s not in UI_SRC)

_REQUIRED_EMITS = (
    "self._win._services_sig.emit(mcp_tools, plugins, brain, wake)",
    "self._win._refresh_ui_sig.emit()",
    "self._win._phone_sig.emit()",
    "self._win._mute_sig.emit(bool(v))",
)
for s in _REQUIRED_EMITS:
    check(f"facade meng-emit sinyal: {s.split('(')[0]}", s in UI_SRC)

# ── 2. sinyal dideklarasikan, disambungkan, dan punya slot ──────────────────
check("_services_sig dideklarasikan", "_services_sig   = pyqtSignal(" in UI_SRC)
check("_refresh_ui_sig dideklarasikan", "_refresh_ui_sig = pyqtSignal()" in UI_SRC)
check("_phone_sig dideklarasikan", "_phone_sig      = pyqtSignal()" in UI_SRC)
check("_mute_sig dideklarasikan", "_mute_sig       = pyqtSignal(bool)" in UI_SRC)
for s in (
    "self._services_sig.connect(self._apply_services)",
    "self._refresh_ui_sig.connect(self._apply_refresh_ui)",
    "self._phone_sig.connect(self.notify_phone_connected)",
    "self._mute_sig.connect(self._apply_mute)",
):
    check(f"sinyal disambungkan: {s.split('.')[1]}", s in UI_SRC)
for s in (
    "def _apply_services(self, mcp_tools, plugins, brain, wake)",
    "def _apply_refresh_ui(self)",
    "def _apply_mute(self, on: bool)",
):
    check(f"slot ada: {s.split('(')[0].replace('def ', '')}", s in UI_SRC)

# hud.set_audio_level / push_visemes SENGAJA tidak lewat sinyal: keduanya
# hanya menulis atribut Python biasa (_live_amp, _visemes) yang dibaca
# _step() di thread Qt — aman di bawah GIL, dan itu tertulis di docstring
# HudCanvas. Yang berbahaya adalah sentuhan widget sungguhan.
check("set_audio_level tetap simpan atribut biasa",
      "if lv > self._live_amp:" in UI_SRC)

# ── 3. _job_verdict: apa yang SEBENARNYA terjadi ────────────────────────────
import main                                                   # noqa: E402


class _Job:
    def __init__(self, status="done", result="", error="", tool="", title="t"):
        self.status, self.result = status, result
        self.error, self.tool = error, tool
        self.title, self.elapsed = title, (lambda: 2.0)


v, _s = main._job_verdict(_Job(result="Email terkirim ke budi@contoh.com "
                                      "— subjek “Rapat”."))
check("sukses → done", v == "done", v)

v, s = main._job_verdict(_Job(
    result="Composio menolak: API key ditolak Composio (401).",
    tool="composio_actions"))
check("kalimat gagal plugin → failed", v == "failed", f"{v} | {s[:40]}")

v, s = main._job_verdict(_Job(
    result="Gmail belum tersambung. Buka panel Connections dulu.",
    tool="composio_actions"))
check("belum tersambung → failed", v == "failed", f"{v} | {s[:40]}")

v, s = main._job_verdict(_Job(
    result="Aksi 'gmail_send' butuh: to, subject, body. Minta keterangan.",
    tool="composio_actions"))
check("argumen kurang → failed", v == "failed", f"{v} | {s[:40]}")

v, s = main._job_verdict(_Job(
    result="KONFIRMASI DIPERLUKAN — kirim email ke budi@contoh.com "
           "dengan subjek “Rapat”.",
    tool="composio_actions"))
check("menunggu konfirmasi ≠ gagal", v == "waiting", v)

v, _s = main._job_verdict(_Job(status="error", error="RuntimeError: boom"))
check("exception → failed", v == "failed", v)

# alat lain yang melaporkan kegagalan sebagai teks biasa
v, _s = main._job_verdict(_Job(result="ERROR: tool 'x' tidak tersedia di latar.",
                               tool="composio_actions"))
check("ERROR teks → failed", v == "failed", v)

# isi email yang kebetulan memuat kata "gagal" tidak boleh salah tuduhan
v, _s = main._job_verdict(_Job(
    result="Email terkirim ke budi@contoh.com — subjek “Laporan gagal "
           "kirim minggu ini”.",
    tool="composio_actions"))
check("subjek mengandung 'gagal' tetap done", v == "done", v)

# kalimat khas plugin, diuji langsung
from plugins import composio_actions as _ca                   # noqa: E402

check("plugin: looks_not_done menolak kalimat konfirmasi",
      not _ca.looks_not_done("KONFIRMASI DIPERLUKAN — kirim email ke "
                             "budi@contoh.com."))
check("plugin: looks_not_done menolak sukses",
      not _ca.looks_not_done("Balasan terkirim di thread 18f0a2."))
check("plugin: looks_not_done menolak daftar hasil",
      not _ca.looks_not_done("3 hasil:\n1. “Rapat” — budi@contoh.com"))
check("plugin: looks_not_done menolak teks kosong? (tidak ada = gagal)",
      _ca.looks_not_done(""))
check("plugin: looks_not_done mengenali Composio bermasalah",
      _ca.looks_not_done("Composio bermasalah: Tidak bisa menghubungi."))
check("plugin: looks_not_done mengenali 'Tidak jadi menjalankan'",
      _ca.looks_not_done("Tidak jadi menjalankan: “x” bukan alamat email "
                         "yang sah."))

# ── 4. Job membawa nama tool dari descriptor ────────────────────────────────
_jr = JobRunner()
_jid = _jr.submit("cek", lambda: "ok",
                  descriptor={"kind": "tool", "tool": "composio_actions",
                              "args": {}})
time.sleep(0.6)
_j = _jr._jobs[_jid]
check("Job.tool dari descriptor", _j.tool == "composio_actions", _j.tool)
_jid2 = _jr.submit("lama", lambda: "ok")
time.sleep(0.6)
check("Job tanpa descriptor → tool kosong", _jr._jobs[_jid2].tool == "")

# ── 5. gerbang TUNGGAL: izin meloloskan, plugin yang mengawal ───────────────
cfg = json.loads((ROOT / "config" / "api_keys.json").read_text(encoding="utf-8"))
_d, _why = permissions.decide("composio_actions", cfg)
check("composio_actions tidak dikonfirmasi dua kali", _d == "allow",
      f"{_d} ({_why})")
_d, _why = permissions.decide("composio_actions", cfg, known=True)
check("composio_actions lolos sebagai tool terdaftar", _d == "allow", _d)
check("send_message tetap minta izin",
      permissions.decide("send_message", cfg)[0] == "confirm")
check("shutdown_jarvis tetap minta izin",
      permissions.decide("shutdown_jarvis", cfg)[0] == "confirm")
check("screen_agent tetap minta izin",
      permissions.decide("screen_agent", cfg)[0] == "confirm")
_ca_src = (ROOT / "plugins" / "composio_actions.py").read_text(encoding="utf-8")
check("plugin mencatat audit sendiri (actor=plugin)",
      "_audit(action, args," in _ca_src and 'actor="plugin"' in _ca_src)

# ── 6. tunda bicara saat otak custom memegang giliran ───────────────────────
check("_on_job_done menunda saat otak bicara",
      "if self.session and not self._in_brain:" in MAIN_SRC)
check("flush di akhir turn otak",
      MAIN_SRC.count("_flush_pending_job_msgs()") >= 2)
check("flush diam tanpa sesi (tidak buang pesan)",
      "if not self.session:\n            return" in MAIN_SRC
      or "if not self.session:" in MAIN_SRC)

for e in errs:
    print("  ERROR:", e)
print("ERRORS:", errs if errs else "none")
