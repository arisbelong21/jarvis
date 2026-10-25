# -*- coding: utf-8 -*-
"""Stage 2+3 verification: header, grid widths, all three HUD centrepieces,
and the animation kill-switch."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QSize, Qt  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))


def probe():
    app = QApplication.instance() or QApplication(sys.argv)
    import ui as uimod
    uimod._hud_fonts()

    ui = uimod.JarvisUI("face.png")
    win = ui._win
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(QSize(1536, 1024))
    win.show()
    app.processEvents(); app.processEvents()

    lines, ok = [], True

    # grid widths (mockup: 230 | fluid | 340)
    lw = win._left_panel.width()
    rw = win._right_panel.width()
    hdr_h = win._header.height() if hasattr(win, "_header") else None
    lines.append(f"grid: left={lw} (mockup 230) right={rw} (mockup 340) "
                 f"header_h={hdr_h} (mockup 68)\n")
    if lw != 230 or rw != 340:
        ok = False

    # clock format check
    win._tick_clock()
    lines.append(f"clock: {win._clock_lbl.text()}  date: {win._date_lbl.text()}\n")

    # three centrepieces
    hud = win.hud
    for style in ("face", "core", "orbit"):
        hud.hud_style = style
        hud._core_phase = 3.7          # a deterministic-looking frame
        hud._blink_tick = 10
        app.processEvents()
        hud.update()
        app.processEvents()
        shot = hud.grab()
        path = os.path.join(OUT, f"stage3_hud_{style}.png")
        shot.save(path)
        ok2 = os.path.getsize(path) > 8000   # not a blank
        ok = ok and ok2
        lines.append(f"hud {style:5s}: rendered {os.path.getsize(path)//1024} KB "
                     f"=> {'OK' if ok2 else 'FAIL'}\n")

    # cycle button
    from memory.config_manager import get_hud_style, save_hud_style
    save_hud_style("face")
    win._toggle_hud_style()
    a = get_hud_style()
    win._toggle_hud_style()
    b = get_hud_style()
    win._toggle_hud_style()
    c = get_hud_style()
    cyc_ok = (a, b, c) == ("core", "orbit", "face")
    ok = ok and cyc_ok
    lines.append(f"hud cycle face->core->orbit->face: {a},{b},{c} "
                 f"=> {'OK' if cyc_ok else 'FAIL'}\n")

    # animation kill switch
    hud.hud_style = "orbit"
    win._toggle_animations()          # -> off
    anim_off = (not hud.animations) and (not hud._tmr.isActive())
    win._toggle_animations()          # -> on
    anim_on = hud.animations and hud._tmr.isActive()
    anim_ok = anim_off and anim_on
    ok = ok and anim_ok
    lines.append(f"animation kill-switch: off={anim_off} on={anim_on} "
                 f"=> {'OK' if anim_ok else 'FAIL'}\n")

    win.close()
    save_hud_style("face")
    with open(os.path.join(OUT, "probe_stage23.txt"), "w", encoding="utf-8") as f:
        f.write("".join(lines))
    print("".join(lines))
    print("ERRORS: none" if ok else "ERRORS: PRESENT")


if __name__ == "__main__":
    probe()
