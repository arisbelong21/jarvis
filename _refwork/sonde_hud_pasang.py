"""Uji integrasi: apakah HudCanvas benar-benar menampilkan wajah 2.5D.

Tidak menjalankan JARVIS penuh (tidak butuh mic/sesi) — hanya membangun
canvas-nya, menampilkan, lalu memeriksa bahwa widget anak muncul dan
menerima status.

Jalankan: python _refwork/sonde_hud_pasang.py
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


print("== A. kode terpasang ==")
src = (ROOT / "ui.py").read_text(encoding="utf-8")
check("ada _ensure_face25d", "_ensure_face25d" in src, True)
check("ada _sync_face25d", "_sync_face25d" in src, True)
check("dipanggil di paintEvent", "_face25 = self._sync_face25d()" in src, True)
check("gaya face25d terpisah dari face", '"face25d"' in src, True)
check("dibuat di showEvent (bukan saat paint)", "def showEvent" in src, True)
f25 = (ROOT / "ui_face25d.py").read_text(encoding="utf-8")
check("widget punya mode transparan", "transparent: bool = False" in f25, True)

print("\n== B. canvas benar-benar menampilkan widget ==")
try:
    from PyQt6.QtWidgets import QApplication

    import ui as UI

    app = QApplication.instance() or QApplication(sys.argv[:1])
    canvas = UI.HudCanvas("face.png")
    canvas.hud_style = "face25d"
    canvas.resize(900, 700)
    canvas.show()
    for _ in range(5):
        app.processEvents()

    w = getattr(canvas, "_face25d", None)
    check("widget wajah dibuat", w is not None, True)
    if w is not None:
        check("widget terlihat", w.isVisible(), True)
        check("ukuran kotak", (w.width() == w.height()), True)
        check("widget transparan", getattr(w, "_transparent", False), True)
        check("status diteruskan", bool(w.state()), True)

        # ganti gaya HUD -> widget harus disembunyikan
        canvas.hud_style = "orbit"
        canvas.update()
        app.processEvents()
        check("gaya orbit menyembunyikan wajah", w.isVisible(), False)

        canvas.hud_style = "face25d"
        canvas.update()
        app.processEvents()
        check("kembali ke face25d menampilkan lagi", w.isVisible(), True)

        # status bicara harus sampai ke widget
        canvas.state = "SPEAKING"
        canvas.update()
        app.processEvents()
        check("status SPEAKING sampai", w.state(), "SPEAKING")
    canvas.close()
except Exception as e:                       # noqa: BLE001
    import traceback
    traceback.print_exc()
    print(f"  (gagal: {e})")

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
