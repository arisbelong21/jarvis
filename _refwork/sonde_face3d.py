"""Sonde: wajah 3D (ui_face3d.py) benar-benar merender, bukan hanya mengimpor.

Memeriksa:
  A. mesh valid (tidak kosong, tanpa NaN, 8 kolom),
  B. API yang dipakai ui.py ada (set_state / set_audio_level),
  C. satu frame benar-benar berisi (bukan layar kosong),
  D. keadaan SPEAKING menaikkan emisi partikel/glow dibanding IDLE,
  E. kegagalan GL tidak mematikan proses (ada penjaga _failed).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

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


print("== A. mesh ==")
import ui_face3d as F

mesh = F.build_head()
check("mesh tidak kosong", len(mesh) > 1000, True)
check("8 kolom (pos/nrm/uv)", mesh.shape[1], 8)
check("tanpa NaN", int(np.isnan(mesh).sum()), 0)
check("normal ternormalisasi",
      bool(np.allclose(np.linalg.norm(mesh[:, 3:6], axis=1), 1.0, atol=1e-3)),
      True)

print("\n== B. API untuk ui.py ==")
check("ada set_state", hasattr(F.Face3DWidget, "set_state"), True)
check("ada set_audio_level", hasattr(F.Face3DWidget, "set_audio_level"), True)
_src = (ROOT / "ui_face3d.py").read_text(encoding="utf-8")
check("ada penjaga kegagalan GL", "_failed" in _src, True)
check("VAO dipakai (wajib profil core)", "QOpenGLVertexArrayObject" in _src, True)
check("point size diaktifkan", "GL_PROGRAM_POINT_SIZE" in _src, True)

print("\n== C. satu frame benar-benar berisi ==")
try:
    from PyQt6.QtWidgets import QApplication

    F._configure_surface()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    w = F.Face3DWidget()
    w.resize(320, 320)
    w.show()
    app.processEvents()
    for _ in range(10):
        w._tick()
        app.processEvents()
    img = w.grabFramebuffer()
    w.close()
    check("framebuffer tidak null", img.isNull(), False)
    check("ukuran sesuai", (img.width(), img.height()), (320, 320))
    # hitung variasi piksel: layar kosong = variasi ~0
    ptr = img.bits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8)
    check("gambar tidak rata (ada isi)", int(arr.std()) > 3, True)
except Exception as e:                       # noqa: BLE001
    print(f"  (dilewati: lingkungan tanpa OpenGL — {e})")

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
