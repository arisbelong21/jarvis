"""Sonde: routing "aksi ke otak custom, perintah kecil ke Gemini".

Keinginan bos (23 Okt): perintah yang butuh berpikir/bertindak (kirim, buat,
kerjakan, edit, hapus, jadwalkan) HARUS ke otak custom supaya hasilnya bagus.
Perintah kecil/cepat (buka youtube, cek jadwal, refresh, cuaca, status) cukup
ditangani Gemini langsung supaya jawabannya cepat.

Syarat yang diuji:
  A. Gemini hanya diberi tool RINGAN — tidak boleh ada tool aksi berat
     (kalau ada, Gemini bisa "mengerjakan sendiri" tugas yang seharusnya
     diserahkan ke otak).
  B. Tool ringan yang disebut bos benar-benar tersedia di sisi Gemini.
  C. Instruksi mulut memuat aturan routing yang eksplisit.
  D. `brain` tetap ada sebagai pintu untuk aksi.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M                                                # noqa: E402
from core.action_loader import discover_actions                  # noqa: E402
from core.plugin_loader import discover_plugins                  # noqa: E402
from core.tool_decls import TOOL_DECLARATIONS                    # noqa: E402

ERRORS = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        ERRORS.append(name)


base = ROOT
a = discover_actions(actions_dir=base / "actions",
                     reserved_names=set(), logger=lambda m: None)
p = discover_plugins(plugins_dir=base / "plugins",
                     core_tool_names=set(), logger=lambda m: None)
ALL = (list(TOOL_DECLARATIONS)
       + a.get_tool_declarations() + p.get_tool_declarations())

# ── A. daftar tool ringan ─────────────────────────────────────────────────
voice = getattr(M, "_VOICE_TOOLS", None)
check("_VOICE_TOOLS tersedia", voice is not None, "tidak ada")

BERAT = {"send_message", "dev_agent", "code_helper", "composio_actions",
         "run_command", "docx_writer", "excel_writer", "cloud_agent",
         "document_review", "meeting_notetaker"}

if voice:
    check("tidak ada tool aksi berat di sisi Gemini",
          not (set(voice) & BERAT), str(set(voice) & BERAT))
    for wajib in ("youtube_video", "calendar_assistant", "browser_control",
                  "open_app", "weather_report", "system_status",
                  "web_search", "video_player"):
        check(f"tool kecil '{wajib}' tersedia untuk Gemini", wajib in voice)

# ── B. penyaringan deklarasi ──────────────────────────────────────────────
vdecls = getattr(M, "_voice_tool_decls", None)
check("_voice_tool_decls tersedia", callable(vdecls), "tidak ada")
if callable(vdecls) and voice:
    picked = vdecls(ALL)
    names = {d.get("name") for d in picked}
    check("hanya tool ringan yang lolos", not (names & BERAT), str(names & BERAT))
    check("deklarasi ringan benar-benar terisi", len(picked) >= 6, str(len(picked)))
    check("tool berat tidak ikut terkirim ke Gemini",
          "dev_agent" not in names and "send_message" not in names)

# ── C. instruksi routing di prompt mulut ──────────────────────────────────
prompt = M._voice_interface_prompt("JARVIS")
low = prompt.lower()
check("prompt mulut menyebut aturan AKSI -> brain",
      "brain" in low and ("aksi" in low or "kerja" in low))
check("prompt mulut menyebut perintah KECIL ditangani langsung",
      "kecil" in low or "ringan" in low or "langsung" in low)
check("prompt mulut melarang brain untuk hal sepele",
      "jangan" in low and ("brain" in low))

# ── D. brain tetap ada ────────────────────────────────────────────────────
check("tool brain tetap dideklarasikan", M._BRAIN_TOOL.get("name") == "brain")

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
