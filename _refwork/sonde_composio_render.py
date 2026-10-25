# -*- coding: utf-8 -*-
"""Render the Connections panel with a realistic Composio catalog, offscreen."""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "composio_connections_panel.png")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    import ui as uimod

    # wiring check: the UI can reach the bridge plugin on its own
    mod = uimod._composio_module()
    assert mod is not None and mod.PLUGIN["name"] == "composio_connector", \
        "ui._composio_module() failed"

    G = "APLIKASI COMPOSIO  ·  composio.dev"
    apps = [
        ("composio:gmail", "Gmail", "Email oleh Google — baca & kirim surat", "connected", "✓ aktif", "ca_gmail_1"),
        ("composio:github", "GitHub", "Repositori, issue, pull request", "connected", "✓ aktif", "ca_github_1"),
        ("composio:googlesheets", "Google Sheets", "Spreadsheet — baca & tulis sel", "setup", "＋", ""),
        ("composio:slack", "Slack", "Pesan channel & DM", "setup", "＋", ""),
        ("composio:notion", "Notion", "Halaman & database", "setup", "＋", ""),
        ("composio:googledrive", "Google Drive", "Berkas & folder", "setup", "＋", ""),
        ("composio:linear", "Linear", "Issue tracking untuk tim", "setup", "＋", ""),
        ("composio:hubspot", "HubSpot", "CRM — kontak & deal", "setup", "＋", ""),
        ("composio:__more__", "… dan 1.592 app lainnya",
         "Pakai kotak pencarian di atas untuk menemukannya", "paused", "", ""),
    ]
    specs = [
        {"kind": "plugin", "id": "telegram_remote", "name": "Telegram",
         "desc": "Kendalikan ARIS dari HP", "status": "connected",
         "fields": [], "values": {}, "keywords": "telegram"},
        {"kind": "mcp", "id": "rea", "name": "rea", "desc": "Server tool MCP",
         "status": "connected", "status_text": "✓ 207 tool",
         "fields": [], "values": {}, "keywords": "rea mcp"},
        {"kind": "plugin", "id": "composio", "name": "Composio",
         "desc": "1.600+ app: Gmail, Notion, GitHub…", "status": "connected",
         "fields": [], "values": {}, "keywords": "composio"},
    ]
    for cid, name, desc, status, text, conn in apps:
        specs.append({"kind": "composio_app", "id": cid, "name": name,
                      "desc": desc, "logo": "", "slug": cid.split(":", 1)[-1],
                      "conn_id": conn, "status": status, "status_text": text,
                      "group": G, "fields": [], "values": {},
                      "keywords": name.lower()})

    cbs = {"reload": lambda: specs,
           "composio_state": lambda: {"busy": False, "gen": 1}}
    host = QWidget(); host.resize(1200, 900)
    ov = uimod.ConnectionsOverlay(specs, cbs, parent=host)
    ov.setGeometry(100, 40, uimod.ConnectionsOverlay._OW, uimod.ConnectionsOverlay._OH)
    ov.show()
    app.processEvents()
    ov.grab().save(OUT)
    print("saved:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
