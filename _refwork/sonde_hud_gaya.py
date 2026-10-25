"""Uji: empat gaya HUD dan kapan widget wajah 2.5D muncul.

Dijalankan sebelum perbaikan -> gaya 'face25d' belum ada -> MERAH.
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


print("== A. kode ==")
src = (ROOT / "ui.py").read_text(encoding="utf-8")
check("gaya face25d dikenal", '"face25d"' in src, True)
check("siklus 4 gaya", '"face": "face25d"' in src, True)
check("label tombol mask", "MASK 2.5D" in src, True)
check("widget hanya aktif di face25d",
      '!= "face25d"' in src, True)

print("\n== B. perilaku tiap gaya ==")
try:
    from PyQt6.QtWidgets import QApplication

    import ui as UI

    app = QApplication.instance() or QApplication(sys.argv[:1])
    c = UI.HudCanvas("face.png")
    c.resize(700, 560)
    c.show()
    app.processEvents()
    w = getattr(c, "_face25d", None)
    check("widget dibuat", w is not None, True)

    for style, should_show in (("face25d", True), ("face", False),
                               ("core", False), ("orbit", False),
                               ("face25d", True)):
        c.hud_style = style
        c.update()
        app.processEvents()
        app.processEvents()
        check(f"gaya {style}: wajah {'tampil' if should_show else 'sembunyi'}",
              w.isVisible() if w else None, should_show)
    c.close()
except Exception as e:                       # noqa: BLE001
    import traceback
    traceback.print_exc()
    print(f"  (gagal: {e})")

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
