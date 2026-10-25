"""Verifikasi fitur baru: katalog terkurasi + penanda managed-auth."""
import importlib.util
import json
import time

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("composio_connector", str(ROOT / "plugins/composio_connector.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

cfg = json.load(open(str(ROOT / "config/api_keys.json"), encoding="utf-8"))["plugin_config"]["composio"]
key, user = cfg["api_key"], cfg["user_id"]
favs = cfg.get("favourite_apps")

errs = []
def check(name, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        errs.append(name)

# 1. cards() tampilan terkurasi (seperti yang akan digambar panel)
specs = mod.cards(key, user, show_all=False, favs=favs)
for _ in range(60):
    if not mod.state().get("busy"):
        break
    time.sleep(1)
specs = mod.cards(key, user, show_all=False, favs=favs)
app_cards = [s for s in specs if s.get("slug")]
more = [s for s in specs if not s.get("slug")]
check("kartu app tampil (di luar kartu loading/more)", len(app_cards) >= 30, f"{len(app_cards)} kartu")
check("Gmail (tersambung) paling atas",
      app_cards and app_cards[0]["slug"] == "gmail" and app_cards[0]["status"] == "connected",
      app_cards[0]["slug"] if app_cards else "-")
check("semua favorit tampil", all(
    s["slug"] in {c["slug"] for c in app_cards} for s in
    [{"slug": f} for f in (favs or [])]), f"{len(favs)} favorit diminta")
check("kartu '…dan N lainnya' ada", len(more) == 1 and "app lainnya" in (more[0]["name"] or ""),
      more[0]["name"] if more else "-")
unmanaged = [s["slug"] for s in app_cards if not s.get("managed", True)]
check("favorit semuanya managed-auth", not unmanaged, f"tanpa managed: {unmanaged[:5]}")
no_auth_ok = [s for s in app_cards if s["status"] == "connected" and not s.get("conn_id")]
check("app tersambung punya conn_id", all(s.get("conn_id") for s in app_cards
      if s["status"] == "connected"), "")

# 2. favs=None -> daftar bawaan
specs2 = mod.cards(key, user, show_all=False, favs=None)
check("favs=None pakai daftar bawaan", len([s for s in specs2 if s.get("slug")]) >= 30)

# 3. show_all=True tetap jalan seperti sebelumnya (head + 400 + kartu "…lainnya")
specs3 = mod.cards(key, user, show_all=True, favs=favs)
all_app = [s for s in specs3 if s.get("slug")]
check("show_all=True -> 400+ kartu (head + 400)", len(all_app) >= 401,
      f"{len(all_app)} kartu app (head = tersambung/re-connect + 400)")

# 4. fav_slugs parsing teks/kommas
check("fav_slugs teks newline", mod.fav_slugs("gmail\nslack, github") == ["gmail", "slack", "github"])
check("fav_slugs list", mod.fav_slugs(["Gmail", " slack "]) == ["gmail", "slack"])
check("fav_slugs kosong -> bawaan", mod.fav_slugs("") == mod._FAV_APPS)

print("\nERRORS:", "none" if not errs else errs)
