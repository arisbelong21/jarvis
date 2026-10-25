"""Sonde: huruf di strip bawah (footer) harus besar & pas dengan kotaknya.

Laporan bos 25 Okt: teks di bagian bawah terlalu kecil dibanding kotaknya,
dan pojok kiri-kanan (LOCAL MEMORY ... / Voice listening ... By ARIS) minta
diperbesar.

Yang diukur, tanpa mengandalkan tangkapan layar:
  A. tinggi huruf nyata (QFontMetrics) di beberapa lebar jendela;
  B. tidak ada yang meluber: jumlah lebar semua anak footer <= lebar footer;
  C. teks penting tetap utuh (tidak terpotong jadi kosong).

Dijalankan offscreen agar JARVIS yang hidup tidak terganggu.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QLabel, QWidget      # noqa: E402
from PyQt6.QtGui import QFontMetrics                            # noqa: E402

import ui as UI                                                 # noqa: E402

ERRORS: list[str] = []
OK = 0


def cek(nama: str, ok: bool, detail: str = "") -> None:
    global OK
    if ok:
        OK += 1
        print(f"  OK    {nama}" + (f" — {detail}" if detail else ""))
    else:
        ERRORS.append(nama)
        print(f"  GAGAL {nama}" + (f" — {detail}" if detail else ""))


app = QApplication.instance() or QApplication(sys.argv[:1])
win = UI.MainWindow("face.png")
win.show()
for _ in range(40):
    app.processEvents()


def footer() -> QWidget | None:
    """Strip bawah.

    CATATAN: ada label lain berbunyi "LOCAL MEMORY" di panel samping, jadi
    pencarian lewat teks pernah menunjuk widget yang salah (1024px dan 1280px
    memberi angka identik 1215 — panel statis, bukan footer). Karena itu
    patokan utamanya `win._footer_w`.
    """
    w = getattr(win, "_footer_w", None)
    if w is not None:
        return w
    for lb in win.findChildren(QLabel):
        if "LOCAL MEMORY" in (lb.text() or ""):
            return lb.parentWidget()
    return None


def tinggi_huruf(w: QWidget) -> int:
    return QFontMetrics(w.font()).height()


# lebar jendela yang diuji: minimum aplikasi, laptop, desktop, ultrawide bos
LEBAR = (820, 1024, 1280, 1600, 1920, 2560, 3437)

print("== A. tinggi huruf & muat di kotak ==")
for W in LEBAR:
    win.resize(W, 900)
    for _ in range(12):
        app.processEvents()
    f = footer()
    if f is None:
        cek(f"{W}px: footer ditemukan", False, "label LOCAL MEMORY tidak ada")
        continue
    anak = [c for c in f.findChildren(QWidget)
            if c.parentWidget() is f and not c.isHidden()]
    # Yang diuji: TIDAK ADA yang terpotong. Ukurannya lebar MINIMUM tiap anak
    # (batas di mana layout tidak lagi bisa menyusutkan), bukan sizeHint —
    # kalimat penjelas memang boleh memendek sendiri (elipsis), jadi sizeHint
    # penuhnya bukan batas bawah.
    perlu = (sum(c.minimumSizeHint().width() for c in anak)
             + 6 * max(0, len(anak) - 1) + 28)     # spacing 6, margin 14+14
    tersedia = f.width()
    label = next(lb for lb in f.findChildren(QLabel)
                 if "LOCAL MEMORY" in (lb.text() or ""))
    h = tinggi_huruf(label)
    muat = perlu <= tersedia + 2
    # target: >=13 px di jendela >=1600, >=11 px di jendela kecil
    target = 13 if W >= 1600 else 11
    cek(f"{W}px: huruf {h}px (target >={target})", h >= target, f"pt={label.font().pointSize()}")
    cek(f"{W}px: tidak ada yang terpotong ({perlu} <= {tersedia})", muat)
    # uji paling keras: tiap label harus punya lebar >= lebar teks yang
    # benar-benar digambarnya. Kalau kurang, Qt memotong tanpa elipsis.
    terpotong = []
    for c in anak:
        if isinstance(c, QLabel):
            butuh = c.fontMetrics().horizontalAdvance(c.text())
            if c.width() + 1 < butuh:
                terpotong.append(f"{c.text()[:18]!r} {c.width()}<{butuh}")
    cek(f"{W}px: tiap teks utuh di kotaknya", not terpotong,
        "; ".join(terpotong))
    # kalimat penjelas: utuh, atau memendek dengan elipsis — jangan kosong
    cap = getattr(win, "_footer_caption", None)
    if cap is not None:
        tampil = cap.text()
        utuh = tampil == cap.fullText()
        wajar = utuh or (tampil.endswith("…") and len(tampil) >= 6)
        cek(f"{W}px: kalimat penjelas {'utuh' if utuh else 'dipendekkan'}",
            wajar, repr(tampil))

print("\n== B. teks penting utuh ==")
win.resize(1600, 900)
for _ in range(12):
    app.processEvents()
f = footer()
if f is not None:
    teks = " | ".join(lb.text() for lb in f.findChildren(QLabel))
    cek("LOCAL MEMORY ada", "LOCAL MEMORY" in teks)
    cek("Voice listening ada", any(
        "Voice listening" in (b.text() or "") for b in f.findChildren(type(win._voice_btn))))
    cek("By ARIS ada", "By ARIS" in teks)
    cek("caption tidak kosong", "riwayat" in teks, teks[:80])

win.close()
print()
print("HASIL:", "HIJAU" if not ERRORS else f"MERAH ({len(ERRORS)} gagal)")
for e in ERRORS:
    print("   -", e)
sys.exit(1 if ERRORS else 0)
