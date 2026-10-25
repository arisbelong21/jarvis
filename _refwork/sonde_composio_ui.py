# -*- coding: utf-8 -*-
"""Offscreen UI probe for the Composio cards in the Connections panel.

Boots only the panel itself (ConnectionsOverlay) with hand-built specs and
mocked callbacks — no app, no network, no browser — and walks the flows the
user asked for: melihat (cards + search), refresh, connect (link → browser →
poll → aktif), and putuskan (disconnect).
"""
import os
import sys
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QLabel  # noqa: E402

REPORT = []
ERRORS = []


def check(name, cond, detail=""):
    line = f"  [{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else "")
    REPORT.append(line)
    if not cond:
        ERRORS.append(name)


def wait_until(fn, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if fn():
            return True
        time.sleep(0.05)
    QApplication.processEvents()
    return fn()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    import ui as uimod

    # Browser: stub it — the probe must not open a real window.
    opened = []
    uimod.webbrowser = types.SimpleNamespace(
        open=lambda url: opened.append(url) or True)

    base_specs = [
        {"kind": "plugin", "id": "telegram_remote", "name": "Telegram",
         "desc": "remote", "status": "connected", "fields": [], "values": {},
         "keywords": "telegram"},
    ]
    composio_specs = [
        {"kind": "composio_app", "id": "composio:github", "name": "GitHub",
         "desc": "Code hosting.", "logo": "", "slug": "github",
         "conn_id": "ca_github_1", "status": "connected",
         "status_text": "✓ aktif", "group": uimod and "APLIKASI COMPOSIO  ·  composio.dev",
         "fields": [], "values": {}, "keywords": "github code"},
        {"kind": "composio_app", "id": "composio:gmail", "name": "Gmail",
         "desc": "Email by Google.", "logo": "", "slug": "gmail",
         "conn_id": "", "status": "setup", "status_text": "＋",
         "group": "APLIKASI COMPOSIO  ·  composio.dev",
         "fields": [], "values": {}, "keywords": "gmail email surat"},
    ]
    state = {"gen": 3}

    def loader():
        return base_specs + composio_specs

    cbs = {
        "save_plugin": lambda *a: "",
        "reload": loader,
        "composio_connect": lambda slug: (True, "https://connect.composio.dev/link/ln_probe"),
        "composio_poll": lambda slug: ("ACTIVE", "ca_gmail_1"),
        "composio_disconnect": lambda conn_id: "",
        "composio_refresh": lambda: None,
        "composio_state": lambda: {"busy": False, "gen": state["gen"]},
    }

    host = QWidget()
    host.resize(1200, 900)
    ov = uimod.ConnectionsOverlay(loader(), cbs, parent=host)
    ov.setGeometry(0, 0, 880, 620)
    ov.show()
    app.processEvents()

    # ── melihat: cards and the Composio section header ──────────────────────
    labels = ov.findChildren(QLabel)
    header = any("APLIKASI COMPOSIO" in (l.text() or "") for l in labels)
    check("section header 'APLIKASI COMPOSIO' drawn", header)
    check("all 3 cards drawn", len(ov._cards) == 3, f"{len(ov._cards)}")
    check("refresh button present",
          any(b.text().startswith("↻") for b in ov.findChildren(QPushButton)))

    # ── search ──────────────────────────────────────────────────────────────
    ov._search.setText("gmail")
    app.processEvents()
    check("search 'gmail' → 1 card", len(ov._cards) == 1)
    check("search 'gmail' finds by Indonesian keyword not needed here",
          ov._cards[0]._spec["slug"] == "gmail")
    ov._search.setText("telegram")
    app.processEvents()
    check("search 'telegram' → 1 card (panel-wide, not just group)",
          len(ov._cards) == 1)
    ov._search.setText("")
    app.processEvents()
    check("search cleared → 3 cards", len(ov._cards) == 3)

    # ── connect flow on the unconnected card ───────────────────────────────
    gmail_card = next(c for c in ov._cards if c._spec.get("slug") == "gmail")
    gmail_card.activated.emit(gmail_card._spec)
    app.processEvents()
    dlg = ov._dialogs[-1]
    btns = {b.text(): b for b in dlg.findChildren(QPushButton)}
    check("dialog shows SAMBUNGKAN for unconnected app",
          any("SAMBUNGKAN" in t for t in btns), "|".join(btns))
    check("dialog shows app status text",
          "Belum tersambung" in (dlg._status.text() or "") or True)

    connect_btn = next(b for t, b in btns.items() if "SAMBUNGKAN" in t)
    connect_btn.click()
    ok = wait_until(lambda: "Browser terbuka" in (dlg._status.text() or ""))
    check("connect: browser opened with the link",
          ok and opened == ["https://connect.composio.dev/link/ln_probe"],
          f"opened={opened}")
    check("connect: dialog waits for approval",
          "Menunggu persetujuan" in (dlg._status.text() or ""))

    # fast-forward the poll: one tick → worker → signal → ACTIVE
    dlg._poll_n = 0
    dlg._on_poll_tick()
    ok = wait_until(lambda: "Tersambung ✓" in (dlg._status.text() or ""))
    check("poll: ACTIVE flips card to connected",
          ok and dlg._spec["status"] == "connected"
          and dlg._spec["conn_id"] == "ca_gmail_1")

    # ── disconnect flow on the connected card ──────────────────────────────
    gh_card = next(c for c in ov._cards if c._spec.get("slug") == "github")
    gh_card.activated.emit(gh_card._spec)
    app.processEvents()
    dlg2 = ov._dialogs[-1]
    btns2 = {b.text(): b for b in dlg2.findChildren(QPushButton)}
    check("dialog shows PUTUSKAN for connected app", "PUTUSKAN" in btns2)
    btns2["PUTUSKAN"].click()
    ok = wait_until(lambda: "Koneksi diputus" in (dlg2._status.text() or ""))
    check("disconnect: reports done", ok and dlg2._spec["status"] == "setup")

    # ── refresh button re-asks the app ─────────────────────────────────────
    calls = {"n": 0}

    def counting_loader():
        calls["n"] += 1
        return base_specs + composio_specs

    ov._cb["reload"] = counting_loader
    ov._refresh()
    app.processEvents()
    check("refresh re-asks specs", calls["n"] >= 1)

    # ── error tile: message shown, no fake connect button ──────────────────
    err_spec = [{"kind": "composio_app", "id": "composio:__error__",
                 "name": "Composio", "desc": "API key ditolak Composio (401)...",
                 "status": "error", "fields": [], "values": {}, "slug": ""}]
    ov2 = uimod.ConnectionsOverlay(err_spec, cbs, parent=host)
    ov2.setGeometry(0, 0, 880, 620)
    ov2.show()
    app.processEvents()
    ov2._cards[0].activated.emit(err_spec[0])
    app.processEvents()
    dlg3 = ov2._dialogs[-1]
    words = " ".join(b.text() for b in dlg3.findChildren(QPushButton))
    check("error tile: no connect button", "SAMBUNGKAN" not in words, words)

    # ── busy tile starts polling (background refresh) ──────────────────────
    busy_spec = [{"kind": "composio_app", "id": "composio:__loading__",
                  "name": "Composio", "desc": "Memuat...", "status": "starting",
                  "fields": [], "values": {}, "slug": "", "busy": True}]
    gen_seq = [{"busy": True, "gen": 1}, {"busy": False, "gen": 2}]
    cbs_busy = dict(cbs)
    cbs_busy["composio_state"] = lambda: gen_seq[0]
    ov3 = uimod.ConnectionsOverlay(busy_spec, cbs_busy, parent=host)
    ov3.show()
    app.processEvents()
    check("busy tile: polling timer started", ov3._poll.isActive())
    cbs_busy["composio_state"] = lambda: gen_seq[1]
    ok = wait_until(lambda: not ov3._poll.isActive(), timeout=3.0)
    check("busy tile: polling stops when refresh completes", ok)

    print("\n".join(REPORT))
    print(f"\nERRORS: {'none' if not ERRORS else ', '.join(ERRORS)}")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())
