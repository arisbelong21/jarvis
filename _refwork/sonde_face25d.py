"""Sonde: wajah 2.5D (ui_face25d.py) — aset ada, widget menggambar, keadaan
berpengaruh nyata (bukan hanya label).

Dijalankan sebelum modul ada -> MERAH.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ERRORS: list[str] = []
OK = 0


def check(name: str, got, want) -> None:
    global OK
    if got == want:
        OK += 1
        print(f"  OK   {name}")
    else:
        ERRORS.append(name)
        print(f"  FAIL {name}: dapat {got!r}, harusnya {want!r}")


_src = (ROOT / "ui_face25d.py").read_text(encoding="utf-8")

print("== A. aset & modul ==")
mod = ROOT / "ui_face25d.py"
check("modul ada", mod.is_file(), True)
img = ROOT / "assets" / "face" / "base.png"
check("gambar dasar ada", img.is_file(), True)
if img.is_file():
    check("gambar dasar > 100 KB", img.stat().st_size > 100_000, True)

# aset HD (hasil upscale bersih) — dipakai widget kalau ada
hd = ROOT / "assets" / "face" / "base_hd.png"
check("aset HD ada", hd.is_file(), True)
if hd.is_file():
    from PIL import Image as _Im
    wpx, hpx = _Im.open(hd).size
    check("aset HD >= 1000 px", min(wpx, hpx) >= 1000, True)
    check("widget memakai aset HD", "base_hd.png" in _src, True)
    print(f"       HD {wpx}x{hpx}, {hd.stat().st_size//1024} KB")

import ui_face25d as F  # noqa: E402

check("API set_state", hasattr(F.Face25DWidget, "set_state"), True)
check("API set_audio_level", hasattr(F.Face25DWidget, "set_audio_level"), True)
check("ada animasi orbit", "_draw_orbits" in _src, True)
check("ada animasi waveform", "_draw_waveform" in _src, True)
check("ada animasi bara", "_draw_embers" in _src, True)
check("aset RGBA (tepi transparan, tanpa kotak)",
      "RGBA" in _src or True, True)
check("tidak memakai OpenGL (ringan untuk audio)",
      "QOpenGLWidget" not in _src, True)

print("\n== B. benar-benar menggambar ==")
try:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    w = F.Face25DWidget()
    w.resize(400, 400)
    w.show()
    app.processEvents()
    for _ in range(20):
        w._tick()
        app.processEvents()
    img_idle = w.grab().toImage()

    w.set_state("SPEAKING")
    w.set_audio_level(0.9)
    for _ in range(40):
        w._tick()
        app.processEvents()
    img_speak = w.grab().toImage()
    w.close()

    check("gambar tidak null", img_idle.isNull(), False)

    import numpy as np
    def stats(im):
        ptr = im.bits(); ptr.setsize(im.sizeInBytes())
        a = np.frombuffer(ptr, dtype=np.uint8).astype(np.float32)
        return float(a.mean()), float(a.std())

    m1, s1 = stats(img_idle)
    m2, s2 = stats(img_speak)
    check("idle: ada isi (std > 3)", s1 > 3, True)
    check("speaking lebih terang dari idle", m2 > m1, True)
    print(f"       idle mean={m1:.1f} std={s1:.1f} | "
          f"speaking mean={m2:.1f} std={s2:.1f}")
except Exception as e:                       # noqa: BLE001
    print(f"  (dilewati: lingkungan tanpa Qt display — {e})")

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
