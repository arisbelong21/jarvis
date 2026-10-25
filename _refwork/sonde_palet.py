# -*- coding: utf-8 -*-
"""Sonde palet — warna teks TIDAK boleh ikut hue aksen.

MERAH terhadap kode SEKARANG: apply_ui_accent menggeser hue TEXT/TEXT_MED/
TEXT_DIM bersama aksen. Dengan aksen hangat (salmon/krem), teks memori di
MemoryOverlay jadi salmon di atas latar near-black — tidak bisa dibaca
(dilaporkan bos 21 Okt, terverifikasi dari tangkapan layar: teks #ffb39f).

Yang diuji: kode palet NYATA dari ui.py (class C, _HUE_LINKED,
_PALETTE_DEFAULTS, apply_ui_accent) dipotong VERBATIM sebagai teks lalu
dijalankan — ui.py sendiri tak bisa diimpor di lingkungan ini (butuh PyQt6).
Yang di-mock: TIDAK ADA. Yang distub: TIDAK ADA.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI_SRC = (ROOT / "ui.py").read_text(encoding="utf-8")

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


# ── potong kode palet verbatim dari ui.py ────────────────────────────────────
def _cut(start: str, end: str) -> str:
    i = UI_SRC.index(start)
    j = UI_SRC.index(end, i + 1)
    return UI_SRC[i:j]

PALET_SRC = _cut("class C:", "def current_palette")

NS: dict = {}
exec(compile(PALET_SRC, "<palet-dari-ui.py>", "exec"), NS)
C = NS["C"]
DEFAULT_TEXT = NS["_PALETTE_DEFAULTS"]["TEXT"]
DEFAULT_MED = NS["_PALETTE_DEFAULTS"]["TEXT_MED"]
DEFAULT_DIM = NS["_PALETTE_DEFAULTS"]["TEXT_DIM"]

check("palet terbaca: TEXT default #d6ecff", DEFAULT_TEXT == "#d6ecff",
      DEFAULT_TEXT)


def lum(hexs: str) -> float:
    def f(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexs[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def kontras(a: str, b: str) -> float:
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# latar MemoryOverlay: rgba(0, 6, 10, 246) — ratakan di atas hitam (kasus
# terburuk: dialog di atas wilayah tergelap).
OVERLAY_BG = "#00060a"

# ── 1. teks harus TETAP di bawaan untuk SETIAP aksen ─────────────────────────
AKSEN = ["#3fd8ff",    # cyan bawaan
         "#ff8a70", "#ff6b4a", "#e05a3a",   # salmon/koral — pemicu laporan
         "#ff2200",    # merah
         "#ffee00",    # kuning
         "#ffffff",    # putih
         "#202020"]    # gelap
for a in AKSEN:
    ns = {}
    exec(compile(PALET_SRC, "<palet>", "exec"), ns)
    ok = ns["apply_ui_accent"](a)
    check(f"aksen {a}: apply_ui_accent berhasil", ok is True)
    c = ns["C"]
    check(f"aksen {a}: TEXT tetap {DEFAULT_TEXT}",
          c.TEXT == DEFAULT_TEXT, c.TEXT)
    check(f"aksen {a}: TEXT_MED tetap {DEFAULT_MED}",
          c.TEXT_MED == DEFAULT_MED, c.TEXT_MED)
    check(f"aksen {a}: TEXT_DIM tetap {DEFAULT_DIM}",
          c.TEXT_DIM == DEFAULT_DIM, c.TEXT_DIM)
    # kontras teks memori vs latar dialog — AA teks normal 4.5:1
    for nama, hexs in (("TEXT", c.TEXT), ("TEXT_MED", c.TEXT_MED),
                       ("TEXT_DIM", c.TEXT_DIM)):
        k = kontras(hexs, OVERLAY_BG)
        check(f"aksen {a}: kontras {nama} vs latar dialog >= 4.5",
              k >= 4.5, f"{k:.2f}:1")

# ── 2. aksen TETAP berlaku untuk yang bukan teks (tema tak mati) ─────────────
ns = {}
exec(compile(PALET_SRC, "<palet>", "exec"), ns)
ns["apply_ui_accent"]("#ff8a70")
check("aksen salmon TETAP menggeser PRI (tema hidup)",
      ns["C"].PRI != NS["_PALETTE_DEFAULTS"]["PRI"], ns["C"].PRI)
check("aksen salmon TETAP menggeser BORDER_B (tema hidup)",
      ns["C"].BORDER_B != NS["_PALETTE_DEFAULTS"]["BORDER_B"], ns["C"].BORDER_B)
# hue digeser, S/V bawaan dipertahankan: PRI cyan -> merah-hangat #ff623f
check("aksen salmon: PRI = hue aksen dgn S/V bawaan",
      ns["C"].PRI == "#ff623f", ns["C"].PRI)

# ── 3. panel memori memakai warna palet (jadi palet terkunci = panel aman) ──
check("ui.py: baris memori pakai C.TEXT",
      'setStyleSheet(f"color: {C.TEXT}; background: transparent;")' in UI_SRC)
check("ui.py: nilai memori pakai C.TEXT_MED",
      "<span style='color:{C.TEXT_MED}'>" in UI_SRC)
check("ui.py: meta memori pakai C.TEXT_DIM",
      'meta.setStyleSheet(f"color: {C.TEXT_DIM}' in UI_SRC)

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
