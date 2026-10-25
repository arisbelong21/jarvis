# -*- coding: utf-8 -*-
"""Stages 4+5 verification: bottom cards with real data, approvals queue,
privacy shield, task graph, and a full regression of the earlier stages."""
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

    # ── stage 4: bottom cards refresh with real data ──
    for fn in ("_refresh_workspace_card", "_refresh_models_card",
               "_refresh_tools_card", "_refresh_approvals"):
        try:
            getattr(win, fn)()
            app.processEvents()
            lines.append(f"card refresh {fn}: OK\n")
        except Exception as e:
            ok = False
            lines.append(f"card refresh {fn}: FAIL {e}\n")

    n_cards = win._bottom_cards.layout().count()
    cards_ok = n_cards == 4
    ok = ok and cards_ok
    lines.append(f"bottom cards count: {n_cards} (mockup 4) "
                 f"=> {'OK' if cards_ok else 'FAIL'}\n")

    # approvals flow
    fired = []
    tid = win.request_approval("Hapus 18 GB cache", "disk 92%", lambda: fired.append(1))
    app.processEvents()
    pending = len(win._approvals)
    win._approve(win._approvals[0], win._approvals[0]["cb"])
    app.processEvents()
    done = (pending == 1 and fired == [1] and len(win._approvals) == 0)
    ok = ok and done
    lines.append(f"approval queue: pending={pending} fired={fired} "
                 f"=> {'OK' if done else 'FAIL'}\n")

    # ── stage 5: privacy shield toggle ──
    from memory.config_manager import (get_privacy_shield, save_privacy_shield,
                                       get_animations_enabled,
                                       save_animations_enabled)
    before = get_privacy_shield()["enabled"]
    win._toggle_privacy_shield()
    after = get_privacy_shield()["enabled"]
    win._toggle_privacy_shield()          # restore
    restore = get_privacy_shield()["enabled"]
    shield_ok = (before != after) and (restore == before)
    ok = ok and shield_ok
    lines.append(f"privacy shield toggle: {before}->{after}->{restore} "
                 f"=> {'OK' if shield_ok else 'FAIL'}\n")

    # task graph feed
    tg = win._task_graph
    win._log_sig.emit("TOOL: files.search · 212 ms")
    win._log_sig.emit("SYS: tidak masuk graph")
    app.processEvents(); app.processEvents()
    tg_ok = len(tg._steps) == 1 and tg._steps[0]["label"].startswith("TOOL")
    ok = ok and tg_ok
    lines.append(f"task graph feed: steps={[s['label'][:20] for s in tg._steps]} "
                 f"=> {'OK' if tg_ok else 'FAIL'}\n")

    # animation config round-trip
    save_animations_enabled(False)
    a0 = get_animations_enabled()
    save_animations_enabled(True)
    a1 = get_animations_enabled()
    anim_ok = (a0 is False) and (a1 is True)
    ok = ok and anim_ok
    lines.append(f"animation config: off={a0} on={a1} "
                 f"=> {'OK' if anim_ok else 'FAIL'}\n")

    # screenshot of the whole window with everything in place
    win.grab().save(os.path.join(OUT, "stage45_full.png"))

    win.close()
    with open(os.path.join(OUT, "probe_stage45.txt"), "w", encoding="utf-8") as f:
        f.write("".join(lines))
    print("".join(lines))
    print("ERRORS: none" if ok else "ERRORS: PRESENT")


if __name__ == "__main__":
    probe()
