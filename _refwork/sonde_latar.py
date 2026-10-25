# -*- coding: utf-8 -*-
"""Sonde latar — daftar memori TIDAK boleh melukis putih.

MERAH terhadap kode SEKARANG: QScrollArea di panel memori hanya diberi
gaya `QScrollArea { background: transparent; }` — itu tak menjangkau
VIEWPORT di dalamnya, dan viewport melukis warna default Qt (putih,
(240,240,240) di Windows). Terverifikasi dari tangkapan layar bos: ketiga
screenshot memuat persis 180.228 piksel (240,240,240) — satu persegi panjang
seukuran viewport — dan teks (salmon dulu, biru-terang sekarang) duduk di
atas putih itu. Widget `inner` di panel memori juga tak punya gaya latar.

Yang diuji: struktur SUMBER ui.py (Qt tak bisa dijalankan di lingkungan
ini). Yang di-mock: TIDAK ADA.
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


def _cut(start: str, end: str) -> str:
    i = UI_SRC.index(start)
    j = UI_SRC.index(end, i + 1)
    return UI_SRC[i:j]


MEM_SRC = _cut("class MemoryOverlay(_HudOverlay):", "\nclass ")

# ── 1. viewport setiap QScrollArea distyle transparan ────────────────────────
n_scroll = UI_SRC.count("QScrollArea()")
n_viewport = UI_SRC.count('viewport().setStyleSheet("background: transparent;")')
check("ui.py: QScrollArea ditemukan", n_scroll == 4, f"{n_scroll} buah")
check("ui.py: SETIAP QScrollArea punya viewport transparan",
      n_viewport == n_scroll, f"viewport transparan {n_viewport}/{n_scroll}")

# ── 2. panel memori: viewport + inner + holder transparan ────────────────────
check("memori: viewport scroll distyle transparan",
      'viewport().setStyleSheet("background: transparent;")' in MEM_SRC)
check("memori: widget inner distyle transparan",
      'inner.setStyleSheet("background: transparent;")' in MEM_SRC)
check("memori: holder tiap baris distyle transparan",
      'holder.setStyleSheet("background: transparent;")' in MEM_SRC)

# ── 3. scrollbar konsisten gelap (bukan default terang) ──────────────────────
check("memori: scrollbar vertikal digaya gelap",
      "QScrollBar:vertical" in MEM_SRC and "QScrollBar::handle:vertical" in MEM_SRC)

# ── 4. latar overlay sendiri TETAP gelap ─────────────────────────────────────
check("memori: latar overlay tetap rgba(0, 6, 10, 246)",
      "rgba(0, 6, 10, 246)" in MEM_SRC)

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
