"""Sonde: efisiensi token otak custom.

Temuan audit (AUDIT-TOKEN-2026-10-23.md): 55 deklarasi tool = ~16.003 token,
dikirim ULANG setiap langkah; satu tugas 8 langkah ~170.000 token.

Yang diuji:
  A. `_CORE_TOOLS` — daftar tool inti yang dikirim setiap giliran, jauh lebih
     kecil dari 55, tetapi tetap memuat semua yang benar-benar dipakai.
  B. `_core_decls` — menyaring deklarasi; ukurannya turun drastis.
  C. `_search_decls` — tool yang dipangkas TETAP bisa ditemukan lewat
     `search_tools`, jadi kemampuan tidak hilang.
  D. `_brain_max_steps_for` — langkah menyesuaikan jenis giliran.
  E. `_provider_notice_enabled` — pengumuman kegagalan provider tidak lagi
     memicu giliran otak baru (satu giliran = ~21.000 token).
"""
import json
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
actions = discover_actions(actions_dir=base / "actions",
                           reserved_names=set(), logger=lambda m: None)
plugins = discover_plugins(plugins_dir=base / "plugins",
                           core_tool_names=set(), logger=lambda m: None)
ALL = (list(TOOL_DECLARATIONS)
       + actions.get_tool_declarations()
       + plugins.get_tool_declarations())
all_chars = len(json.dumps(ALL, ensure_ascii=False, default=str))

# ── A. daftar inti ────────────────────────────────────────────────────────
core = getattr(M, "_CORE_TOOLS", None)
check("_CORE_TOOLS tersedia", core is not None, "tidak ada")
if core:
    check("jumlah tool inti <= 24", len(core) <= 24, f"{len(core)}")
    wajib = {"file_controller", "code_helper", "dev_agent", "run_command",
             "web_search", "composio_actions", "send_message", "docx_writer",
             "recall_memory", "save_memory", "model_status", "switch_model",
             "screen_process", "weather_report", "browser_control",
             "computer_control", "system_status", "open_app"}
    kurang = sorted(wajib - set(core))
    check("semua tool yang sering dipakai ada di inti", not kurang, str(kurang))

# ── B. penyaringan deklarasi ──────────────────────────────────────────────
core_decls = getattr(M, "_core_decls", None)
check("_core_decls tersedia", callable(core_decls), "tidak ada")
if callable(core_decls) and core:
    picked = core_decls(ALL)
    chars = len(json.dumps(picked, ensure_ascii=False, default=str))
    names = {d.get("name") for d in picked}
    check("ukuran deklarasi turun minimal 50%",
          chars <= all_chars * 0.5, f"{chars} vs {all_chars}")
    check("tidak ada tool inti yang hilang", not (set(core) - names),
          str(sorted(set(core) - names)))
    check("tool langka tidak ikut dikirim (mis. quiz)",
          "quiz" not in names, str(sorted(names))[:80])

# ── C. tool yang dipangkas tetap bisa dicari ──────────────────────────────
search = getattr(M, "_search_decls", None)
check("_search_decls tersedia", callable(search), "tidak ada")
if callable(search):
    hits = search(ALL, "quiz")
    check("tool yang dipangkas tetap ditemukan", bool(hits), str(len(hits)))
    if hits:
        check("hasil pencarian berisi tool yang tepat",
              any(d.get("name") == "quiz" for d in hits),
              str([d.get("name") for d in hits]))
    check("pencarian kosong tidak mengembalikan sampah",
          search(ALL, "zzz_tidak_ada") == [], "harus kosong")

# ── D. langkah adaptif ────────────────────────────────────────────────────
steps = getattr(M, "_brain_max_steps_for", None)
check("_brain_max_steps_for tersedia", callable(steps), "tidak ada")
if callable(steps):
    check("work_request memakai batas penuh", steps("work_request", 12) == 12,
          str(steps("work_request", 12)))
    check("recall lebih pendek", steps("recall", 12) <= 6,
          str(steps("recall", 12)))
    check("confirmation sangat pendek", steps("confirmation", 12) <= 3,
          str(steps("confirmation", 12)))
    check("jenis tak dikenal tidak dipotong berlebihan",
          steps("sesuatu", 12) >= 8, str(steps("sesuatu", 12)))

# ── E. pengumuman kegagalan tidak memicu giliran ──────────────────────────
notice = getattr(M, "_provider_notice_enabled", None)
check("_provider_notice_enabled tersedia", callable(notice), "tidak ada")
if callable(notice):
    # 24 Okt — bos minta SETIAP error otak disampaikan. Notifikasi kini
    # diucapkan MULUT (Gemini) langsung, tanpa memanggil provider otak,
    # jadi tidak lagi menambah satu giliran ~12.000 token.
    check("default: error otak DIKABARKAN (permintaan bos)",
          notice() is True, str(notice()))

# ── F. pelampiran tool berbasis kata kunci ────────────────────────────────
hints = getattr(M, "_hinted_tool_names", None)
check("_hinted_tool_names tersedia", callable(hints), "tidak ada")
if callable(hints):
    check("'pomodoro' melampirkan tool pomodoro",
          "pomodoro" in hints("mulai timer pomodoro 25 menit"))
    check("ucapan biasa tidak melampirkan apa pun",
          hints("buat folder di desktop") == set(),
          str(hints("buat folder di desktop")))
    check("'pengingat' melampirkan reminder",
          "reminder" in hints("buat pengingat besok pagi"))

hdecls = getattr(M, "_hinted_decls", None)
check("_hinted_decls tersedia", callable(hdecls), "tidak ada")
if callable(hdecls) and core:
    picked = hdecls(ALL, "mulai timer pomodoro")
    names = {d.get("name") for d in picked}
    check("hint melampirkan deklarasi pomodoro", "pomodoro" in names, str(names))
    check("hint tidak menduplikasi tool inti",
          not (names & set(core)), str(names & set(core)))
    check("tanpa kata kunci -> tidak ada tambahan",
          hdecls(ALL, "buat folder") == [])

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
