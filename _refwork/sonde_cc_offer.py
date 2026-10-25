"""Sonde: tawaran sebelum mengambil alih mouse/keyboard.

Keputusan bos (23 Okt): tugas berat mouse/keyboard dialihkan ke otak custom
supaya tidak salah keputusan — TETAPI Gemini harus MENAWARKAN dulu, dan bos
yang memilih.

Yang diuji:
  A. `_cc_is_heavy()` — aksi mana yang berat (mengetik/rangkaian) vs ringan.
  B. `_cc_choice()` — membaca pilihan bos: "otak/custom" vs "gemini/kamu saja".
  C. `_cc_offer_fresh()` — tawaran kedaluwarsa supaya tidak nyangkut.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M                                                # noqa: E402

ERRORS = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        ERRORS.append(name)


# ── A. aksi berat vs ringan ───────────────────────────────────────────────
heavy = getattr(M, "_cc_is_heavy", None)
check("_cc_is_heavy ada", callable(heavy), "tidak ada")
if callable(heavy):
    BERAT = ["type", "smart_type", "paste", "copy", "drag", "hotkey",
             "clear_field", "screen_click", "random_data", "user_data"]
    for a in BERAT:
        check(f"berat: {a}", heavy({"action": a}) is True)
    RINGAN = ["screenshot", "screen_find", "move", "scroll", "wait",
              "focus_window"]
    for a in RINGAN:
        check(f"ringan: {a}", heavy({"action": a}) is False)
    check("klik DENGAN target jelas -> ringan",
          heavy({"action": "click", "text": "Simpan"}) is False)
    check("klik TANPA target -> berat (klik buta berbahaya)",
          heavy({"action": "click"}) is True)
    check("aksi tak dikenal -> tidak dianggap berat",
          heavy({"action": "sesuatu"}) is False)


# ── B. membaca pilihan bos ────────────────────────────────────────────────
choice = getattr(M, "_cc_choice", None)
check("_cc_choice ada", callable(choice), "tidak ada")
if callable(choice):
    for t in ("pakai otak custom saja", "alihkan ke otak", "custom api",
              "serahkan ke custom"):
        check(f"-> brain: {t[:26]!r}", choice(t) == "brain", str(choice(t)))
    for t in ("kamu saja", "gemini saja", "kamu aja yang kerjakan",
              "kerjakan sendiri"):
        check(f"-> gemini: {t[:26]!r}", choice(t) == "gemini", str(choice(t)))
    for t in ("jangan pakai otak", "buka youtube", "ya", "tidak"):
        check(f"-> None: {t[:26]!r}", choice(t) is None, str(choice(t)))


# ── C. tawaran kedaluwarsa ────────────────────────────────────────────────
fresh = getattr(M, "_cc_offer_fresh", None)
check("_cc_offer_fresh ada", callable(fresh), "tidak ada")
if callable(fresh):
    check("tawaran baru masih berlaku", fresh(1000.0, 1030.0) is True)
    check("tawaran kedaluwarsa", fresh(1000.0, 9999.0) is False)
    check("belum ada tawaran", fresh(0.0, 1000.0) is False)

# ── D. sambungan nyata ────────────────────────────────────────────────────
SRC = (ROOT / "main.py").read_text(encoding="utf-8")
check("gerbang tawaran dipasang di _execute_tool",
      "_cc_is_heavy(args)" in SRC)
check("pilihan bos ditangani sebelum eksekusi",
      "_cc_choice(self._live_utterance" in SRC)
check("ada penanda persetujuan sekali pakai", "_cc_approved" in SRC)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
