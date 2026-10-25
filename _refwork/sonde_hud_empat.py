"""Sonde: KEEMPAT gaya HUD benar-benar hidup dan berbeda.

Bos 25 Okt: "kamu ngilangin 2 animasi lainnya ... malah jadi 2 animasi".
Sonde ini menembak gambar tiap gaya dua kali (jeda ~0,45 s) lalu mengukur:

  1. tiap gaya menghasilkan gambar yang BERUBAH  -> animasinya jalan;
  2. gambar antar gaya saling BERBEDA            -> benar 4 tampilan, bukan
                                                    satu tampilan diulang;
  3. widget wajah 2.5D hanya tampil di gaya face25d.

Ambang "berubah" 0,3% piksel: cukup untuk gerakan halus (bara, denyut),
tapi tetap menolak frame beku. Dijalankan tanpa layar (offscreen).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_refwork"))

from PyQt6.QtWidgets import QApplication          # noqa: E402
from PyQt6.QtGui import QImage                    # noqa: E402

import ui as UI                                   # noqa: E402

GAYA = ("face", "face25d", "core", "orbit")
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


def beda(a: QImage, b: QImage) -> float:
    """Persentase piksel yang berbeda nyata (selisih > 8 per kanal)."""
    if a.size() != b.size() or a.isNull() or b.isNull():
        return 100.0
    a = a.convertToFormat(QImage.Format.Format_RGB32)
    b = b.convertToFormat(QImage.Format.Format_RGB32)
    w, h = a.width(), a.height()
    total = w * h
    beda_n = 0
    for y in range(0, h, 2):                      # sampel tiap 2 baris: cepat
        ra = a.constScanLine(y).asstring(w * 4)
        rb = b.constScanLine(y).asstring(w * 4)
        for x in range(0, w * 4, 16):             # tiap 4 piksel
            if (abs(ra[x] - rb[x]) > 8 or abs(ra[x + 1] - rb[x + 1]) > 8
                    or abs(ra[x + 2] - rb[x + 2]) > 8):
                beda_n += 1
    sampel = (h // 2) * ((w * 4) // 16)
    return 100.0 * beda_n / max(1, sampel)


app = QApplication.instance() or QApplication(sys.argv[:1])
c = UI.HudCanvas("face.png")
c.resize(760, 600)
c.show()
app.processEvents()

def tangkap(style: str) -> QImage:
    c.hud_style = style
    c.update()
    for _ in range(3):
        app.processEvents()
    time.sleep(0.22)
    for _ in range(3):
        app.processEvents()
    return c.grab().toImage()


print("== 1. animasi hidup per gaya ==")
frames: dict[str, tuple[QImage, QImage]] = {}
for s in GAYA:
    a = tangkap(s)
    time.sleep(0.45)
    for _ in range(4):
        app.processEvents()
    b = tangkap(s)
    frames[s] = (a, b)
    d = beda(a, b)
    cek(f"{s}: bergerak", d > 0.3, f"beda frame {d:.2f}%")

print("\n== 2. empat gaya saling berbeda ==")
for i, s1 in enumerate(GAYA):
    for s2 in GAYA[i + 1:]:
        d = beda(frames[s1][0], frames[s2][0])
        cek(f"{s1} != {s2}", d > 1.0, f"beda {d:.2f}%")

print("\n== 3. widget 2.5D hanya di face25d ==")
w = getattr(c, "_face25d", None)
for s in GAYA:
    c.hud_style = s
    c.update()
    for _ in range(3):
        app.processEvents()
    tampil = bool(w and w.isVisible())
    cek(f"{s}: widget {'tampil' if tampil else 'sembunyi'}",
        tampil == (s == "face25d"))

c.close()
print()
print("HASIL:", "HIJAU" if not ERRORS else f"MERAH — {ERRORS}")
sys.exit(1 if ERRORS else 0)
