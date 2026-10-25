"""Verifikasi akhir perbaikan:
1. fetch_catalog -> harus ~1.600 toolkit (bukan 0)
2. cards() dengan user_id dari config -> kartu app muncul, Gmail ✓ aktif
3. /tools is_deprecated shape -> pastikan composio_actions tidak terdampak
"""
import importlib.util
import json

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

PLUGIN = str(ROOT / "plugins/composio_connector.py")
CONFIG = str(ROOT / "config/api_keys.json")
spec = importlib.util.spec_from_file_location("composio_connector", PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

cfg = json.load(open(CONFIG, encoding="utf-8"))["plugin_config"]["composio"]
key = cfg["api_key"]
user = cfg["user_id"]
print(f"user_id config: {user}")

# 1. katalog
cat = mod.fetch_catalog(key)
print(f"1) fetch_catalog -> {len(cat)} toolkit | contoh: {[c['slug'] for c in cat[:5]]}")
gmail_in_cat = [c for c in cat if c["slug"] == "gmail"]
print(f"   Gmail di katalog: {gmail_in_cat and (gmail_in_cat[0]['name'], 'deprecated=' + str(gmail_in_cat[0]['deprecated']))}")

# 2. kartu seperti yang digambar panel (tunggu refresh background)
specs = mod.cards(key, user)
for _ in range(60):
    if not mod.state().get("busy"):
        break
    import time; time.sleep(1)
specs = mod.cards(key, user)
conn = [s for s in specs if s.get("status") == "connected" and s.get("slug")]
print(f"2) cards() -> {len(specs)} kartu | tersambung: {[s.get('slug') for s in conn]}")
gm = [s for s in specs if s.get("slug") == "gmail"]
print(f"   kartu Gmail: {[(s.get('status'), s.get('status_text')) for s in gm]}")

# 3. shape is_deprecated di endpoint /tools
tools = mod._api(key, "GET", "/tools", params={"toolkit_slug": "gmail", "limit": 3})
t0 = (tools.get("items") or [{}])[0]
print(f"3) /tools gmail: {len(tools.get('items') or [])} item | is_deprecated = {t0.get('is_deprecated')!r}")
