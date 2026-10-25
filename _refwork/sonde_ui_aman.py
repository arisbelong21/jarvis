# -*- coding: utf-8 -*-
"""Sonde UI aman — timer yang menyentuh widget terhapus tidak boleh mematikan app.

MERAH sebelum perbaikan: menghapus provider membangun ulang kartu, tombol lama
dihapus Qt, lalu `QTimer.singleShot(4000, lambda: btn.setText("×"))` menyentuh
objek yang sudah mati → RuntimeError → proses berhenti (exit -1073740791).

Yang diuji: kode NYATA ui.py + objek Qt sungguhan. Tanpa PyQt6 sonde ini
dilewati, bukan dipalsukan.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


SRC = (ROOT / "ui.py").read_text(encoding="utf-8")

print("== struktur ==")
check("ada penjaga _safe_later", "def _safe_later(" in SRC)
check("hanya satu QTimer.singleShot tersisa (di dalam penjaga)",
      SRC.count("QTimer.singleShot") == 1, SRC.count("QTimer.singleShot"))
check("penjaga dipakai di >= 4 tempat",
      SRC.count("_safe_later(") - 1 >= 4, SRC.count("_safe_later(") - 1)
check("pola lama (timer menyentuh btn) sudah tidak ada",
      'QTimer.singleShot(4000, lambda: (btn.setText' not in SRC)

print("== uji Qt sungguhan ==")
try:
    from PyQt6.QtWidgets import QApplication, QPushButton
    from PyQt6 import sip
    from PyQt6.QtCore import QEventLoop, QTimer
except Exception:                                        # noqa: BLE001
    print("No module named 'PyQt6'")     # penanda SKIP untuk run_tests
    raise SystemExit(0)

app = QApplication.instance() or QApplication([])
from ui import _safe_later                              # noqa: E402

btn = QPushButton("x")
called = {"n": 0}


def _cb():
    called["n"] += 1
    btn.setText("y")          # objek yang sudah dihapus — dulu ini mematikan app


_safe_later(50, _cb)
sip.delete(btn)               # meniru panel yang dibangun ulang
loop = QEventLoop()
QTimer.singleShot(300, loop.quit)
loop.exec()

check("callback berjalan setelah widget dihapus", called["n"] == 1, called["n"])
check("proses tetap hidup (tidak ada RuntimeError yang lolos)", True, "selamat")

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
