"""Sonde: Live Task Graph harus benar-benar diberi data pekerjaan nyata.

Temuan 23 Okt: widget `TaskGraph` (visual node ◆ berjalan / ✓ selesai, gaya
coding agent) SUDAH ada dan sudah dipasang di stage — tetapi hanya diberi
makan oleh teks log yang cocok dengan pola tertentu, dan pola itu hampir
tidak pernah muncul. Hasilnya: panel kosong meski pekerjaan berjalan, dan
bos hanya melihat satu baris teks kecil.

Sonde ini menjaga tiga hal:
  A. status dinormalkan (ok/run/wait) — bukan string bebas
  B. jalur thread-safe ada (signal), karena otak & job berjalan di thread lain
  C. main.py benar-benar memanggilnya di titik-titik pekerjaan
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ui as UI                                                 # noqa: E402

ERRORS = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        ERRORS.append(name)


# ── A. normalisasi status ─────────────────────────────────────────────────
norm = getattr(UI, "_tg_norm_status", None)
check("_tg_norm_status ada", callable(norm), "tidak ada")
if callable(norm):
    check("'ok' tetap ok", norm("ok") == "ok")
    check("'selesai' -> ok", norm("selesai") == "ok")
    check("'queued' -> wait", norm("queued") == "wait")
    check("status tak dikenal -> run", norm("xyz") == "run")
    check("string kosong -> run", norm("") == "run")

# ── B. jalur thread-safe ──────────────────────────────────────────────────
MW = getattr(UI, "MainWindow", None)
check("MainWindow punya signal _tg_sig",
      MW is not None and hasattr(MW, "_tg_sig"), "tidak ada")
check("MainWindow punya slot _apply_task_graph",
      MW is not None and hasattr(MW, "_apply_task_graph"), "tidak ada")

JU = getattr(UI, "JarvisUI", None)
check("JarvisUI punya task_mark",
      JU is not None and hasattr(JU, "task_mark"), "tidak ada")
check("JarvisUI punya task_clear",
      JU is not None and hasattr(JU, "task_clear"), "tidak ada")

TG = getattr(UI, "TaskGraph", None)
check("TaskGraph punya mark & clear",
      TG is not None and hasattr(TG, "mark") and hasattr(TG, "clear"))

# ── C. pemanggilan nyata di main.py ───────────────────────────────────────
SRC = (ROOT / "main.py").read_text(encoding="utf-8")
for titik, frasa in (
        ("langkah pekerjaan latar (_job_step)", 'self.ui.task_mark(str(text)[:38], "run")'),
        ("tool otak (reasoning)", 'self.ui.task_mark(name, "run")'),
        ("pekerjaan selesai", 'self.ui.task_mark(f"{title[:30]} — {verdict}"'),
        ("pekerjaan baru", "self.ui.task_clear()"),
):
    check(f"main.py memanggil grafik di {titik}", frasa in SRC)

# ── D. grafik tidak boleh hanya bergantung pada teks log ──────────────────
check("mark() menutup node 'run' sebelumnya",
      'self._steps[-1]["status"] = "ok"' in
      (ROOT / "ui.py").read_text(encoding="utf-8"))

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
