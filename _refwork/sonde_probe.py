# -*- coding: utf-8 -*-
"""Offscreen render probe for the restructured ARIS UI (reference #2 layout)."""
import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QSize  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))

SIZES = [(1536, 1024), (1280, 800)]
REF = dict(left=14.0, right=23.2, cards=24.0)


def pct(v, tot):
    return (v / tot * 100.0) if tot else 0.0


def probe():
    app = QApplication.instance() or QApplication(sys.argv)
    import ui as uimod

    win = uimod.JarvisUI("face.png")
    uiwin = win._win
    uiwin.setAttribute(__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.WidgetAttribute.WA_DontShowOnScreen, True)

    report = []
    ok = True
    try:
        for w, h in SIZES:
            uiwin.resize(QSize(w, h))
            uiwin.show()
            app.processEvents()
            app.processEvents()

            side_w = uiwin._left_panel.width() if hasattr(uiwin, "_left_panel") else None
            rail_w = uiwin._right_panel.width()
            cards_h = uiwin._bottom_cards.height()

            left_p = pct(side_w or 0, w)
            right_p = pct(rail_w, w)
            cards_p = pct(cards_h, h)
            line = f"=== {w}x{h} ===\n"
            line += f"  sidebar    {side_w} px = {left_p:5.1f}%   (ref {REF['left']}%)\n"
            line += f"  right rail {rail_w} px = {right_p:5.1f}%   (ref {REF['right']}%)\n"
            line += f"  card strip {cards_h} px = {cards_p:5.1f}%   (ref {REF['cards']}%)\n"
            report.append(line)

            # sanity: widget existence
            names = [
                "_sys_status", "_bottom_cards", "_right_panel", "_hud_cam_stack",
                "_input", "_input_center", "_qc_subs", "_log",
            ]
            missing = [n for n in names if not hasattr(uiwin, n)]
            if missing:
                ok = False
                line2 = f"  MISSING ATTRS: {missing}\n"
                report.append(line2)

        # code-path tests at 1536x1024
        uiwin.resize(QSize(1536, 1024))
        app.processEvents()
        tests = []
        tests.append(("sys_status refresh", lambda: uiwin._sys_status.refresh(
            {"cpu": 42.0, "mem": 61.0, "disk": 73.0, "gpu": -1.0,
             "net_down": 1_250_000.0, "net_up": 310_000.0})))
        tests.append(("qc_subs refresh", lambda: uiwin._refresh_qc_subs()))
        tests.append(("nav_workflows", lambda: uiwin._nav_workflows()))
        tests.append(("ambient toggle", lambda: uiwin._ambient_toggle()))
        tests.append(("send via center", lambda: (uiwin._input_center.setText("tes pusat"), uiwin._send_center())))
        tests.append(("send via rail", lambda: (uiwin._input.setText("tes rail"), uiwin._send("rail"))))
        for name, fn in tests:
            try:
                fn()
                app.processEvents()
                report.append(f"  TEST OK   : {name}\n")
            except Exception:
                ok = False
                report.append(f"  TEST FAIL : {name}\n{traceback.format_exc()}\n")

        uiwin._ambient_toggle()  # restore undimmed
        app.processEvents()
        shot = uiwin.grab()
        shot.save(os.path.join(OUT, "aris2_1536x1024.png"))
        uiwin.resize(QSize(1280, 800))
        app.processEvents()
        uiwin.grab().save(os.path.join(OUT, "aris2_1280x800.png"))
        report.append("RENDERS SAVED\n")
    except Exception:
        ok = False
        report.append("FATAL:\n" + traceback.format_exc() + "\n")

    uiwin.close()
    with open(os.path.join(OUT, "probe_final.txt"), "w", encoding="utf-8") as f:
        f.write("".join(report))
    print("".join(report))
    print("ERRORS: none" if ok else "ERRORS: PRESENT")


if __name__ == "__main__":
    probe()
