# -*- coding: utf-8 -*-
"""Verify the CONTROLS / SETUP drawers open centred and unclipped."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QSize  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))


def probe():
    app = QApplication.instance() or QApplication(sys.argv)
    import ui as uimod

    ui = uimod.JarvisUI("face.png")
    win = ui._win
    from PyQt6.QtCore import Qt
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)

    lines = []
    ok = True
    for w, h in [(1536, 1024), (1280, 800)]:
        win.resize(QSize(w, h))
        win.show()
        app.processEvents(); app.processEvents()
        cw = win.centralWidget()
        cw_w = cw.width()

        for name in ("_ctrl_drawer", "_quick_drawer"):
            # open + position
            win._close_controls()
            win._close_setup()
            if name == "_ctrl_drawer":
                win._ctrl_btn.setChecked(True)
                win._toggle_controls(True)
            else:
                win._drawer_btn.setChecked(True)
                win._toggle_drawer(True)
            app.processEvents()

            # allow one layout pass after show(), then re-place
            app.processEvents()
            if name == "_ctrl_drawer":
                win._position_ctrl_drawer()
            else:
                win._position_quick_drawer()
            app.processEvents()

            d = getattr(win, name)
            vis = d.isVisible()
            x, wid = d.x(), d.width()
            centred_off = abs((cw_w - wid) // 2 - x)
            clipped_right = x + wid > cw_w
            clipped_left = x < 0
            good = vis and not clipped_right and not clipped_left and centred_off <= 2
            ok = ok and good
            lines.append(
                f"  {w}x{h} {name}: visible={vis} x={x} w={wid} "
                f"cw={cw_w} | centred_off={centred_off}px "
                f"clip_right={clipped_right} clip_left={clipped_left} "
                f"=> {'OK' if good else 'FAIL'}\n"
            )

        # screenshot with the controls drawer open
        if (w, h) == (1536, 1024):
            win._close_setup()
            win._ctrl_btn.setChecked(True)
            win._toggle_controls(True)
            app.processEvents()
            win.grab().save(os.path.join(OUT, "aris2_controls_centred.png"))

    win.close()
    with open(os.path.join(OUT, "probe_drawer.txt"), "w", encoding="utf-8") as f:
        f.write("".join(lines))
    print("".join(lines))
    print("ERRORS: none" if ok else "ERRORS: PRESENT")


if __name__ == "__main__":
    probe()
