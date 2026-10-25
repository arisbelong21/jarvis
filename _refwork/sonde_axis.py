# -*- coding: utf-8 -*-
"""Probe: presisi axis HUD — kepala orbit di tengah cincin, animasi segaris
dengan chip LATENSI/dll, nav kiri diperbesar, tombol mode dihapus."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402
app = QApplication(sys.argv)              # noqa: E402

import ui  # noqa: E402

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


win = ui.JarvisUI("face.png")._win
win.resize(1536, 1024)
win.show()
app.processEvents()
win.hud.hud_style = "orbit"

# 1) mode buttons gone
check("mode buttons removed", not getattr(win, "_mode_btns", {}))
check("method _position_mode_btns removed",
      not hasattr(win, "_position_mode_btns"))
check("method _refresh_mode_btns removed",
      not hasattr(win, "_refresh_mode_btns"))

# 2) nav buttons enlarged
b = win._nav_btns[0]
check("nav rows taller", b.minimumHeight() >= 42,
      f"minHeight={b.minimumHeight()}px")
check("nav font bigger", b.font().pointSize() >= 12,
      f"{b.font().pointSize()}pt")

# 3) axis alignment: axis_shift must equal (command centre in HUD coords)
#    minus half the HUD width — computed from global geometry directly.
win._align_hud_axis()
app.processEvents()
hud = win.hud
hud_tlGlobal = hud.mapTo(win, hud.rect().topLeft())
split_tlGlobal = win._center_split.mapTo(win, win._center_split.rect().topLeft())
cmd_cx_local = (split_tlGlobal.x() + win._center_split.width() // 2
                - hud_tlGlobal.x())
expected_shift = cmd_cx_local - hud.width() / 2.0
check("axis_shift set & sane",
      abs(getattr(hud, "axis_shift", 999)) < 200,
      f"axis_shift={getattr(hud, 'axis_shift', 0):.1f}px")
check("axis_shift == theoretical offset",
      abs(getattr(hud, "axis_shift", 0) - expected_shift) <= 2,
      f"shift={getattr(hud, 'axis_shift', 0):.1f} expected {expected_shift:.1f}")

# 4) render the orbit and measure head vs ring centres in pixels
pm = hud.grab()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "axis_orbit_check.png")
pm.save(out)

from PIL import Image
import numpy as np
im = Image.open(out).convert("RGB")
a = np.asarray(im).astype(int)
H, W = a.shape[:2]
r_, g_, b_ = a[..., 0], a[..., 1], a[..., 2]

# Cincin & kepala itu TERANG; kisi atom dan garis medan sengaja redup.
# PENTING: warna aksen bisa diganti pengguna (hue), jadi mask tidak boleh
# mengasumsikan cyan — ia mengikuti warna C.PRI yang sedang aktif.
from PyQt6.QtGui import QColor  # noqa: E402
_acc = QColor(ui.C.PRI)
_ar, _ag, _ab = _acc.red(), _acc.green(), _acc.blue()


def _accent_mask(arr):
    rr, gg, bb = arr[..., 0], arr[..., 1], arr[..., 2]
    dist = (np.abs(rr - _ar) + np.abs(gg - _ag) + np.abs(bb - _ab))
    return (dist < 130) & ((rr + gg + bb) > 300)


bright = _accent_mask(a)
by, bx = np.nonzero(bright)
if len(bx) < 50:
    # Jendela belum selesai digambar saat grab() — paksa satu repaint.
    hud.repaint()
    app.processEvents()
    pm = hud.grab()
    pm.save(out)
    a = np.asarray(Image.open(out).convert("RGB")).astype(int)
    H, W = a.shape[:2]
    bright = _accent_mask(a)
    by, bx = np.nonzero(bright)

if len(bx) < 50:
    check("orbit rings centred on axis", False,
          "tidak ada piksel aksen terang — HUD tidak ter-render")
else:
    ring_left, ring_right = bx.min(), bx.max()
    ring_cx = (ring_left + ring_right) / 2.0
    # Yang diuji adalah invarian tata letak: cincin digambar pada cx yang
    # sama dengan kepala, dan cx = W/2 + axis_shift. Mengukur centroid kepala
    # dari piksel tidak lagi bisa diandalkan sejak ada kisi atom — elemen itu
    # juga memakai warna aksen dan menggeser median beberapa piksel.
    expected_cx = W / 2.0 + float(getattr(hud, "axis_shift", 0.0))
    check("orbit rings centred on axis",
          abs(ring_cx - expected_cx) <= 6,
          f"ring cx={ring_cx:.1f} vs axis={expected_cx:.1f} "
          f"(aksen {ui.C.PRI})")
print(f"  render: {out}")

for t in errs:
    print("  ERROR:", t)
print("ERRORS:", errs if errs else "none")
