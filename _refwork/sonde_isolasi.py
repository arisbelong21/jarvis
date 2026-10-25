"""Isolasi: panggil fungsi plugin langsung di main thread, lalu uji cards()
dengan user_id koneksi asli (pg-test-...) untuk membuktikan hipotesis user_id.
"""
import importlib.util
import json

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

PLUGIN = str(ROOT / "plugins/composio_connector.py")
spec = importlib.util.spec_from_file_location("composio_connector", PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

key = "ak_L4S_h1S7YBoMG_rejL80"

print("_urlopen:", mod._urlopen.__module__ + "." + mod._urlopen.__qualname__)
d = mod._api(key, "GET", "/toolkits", params={"limit": 200, "sort_by": "alphabetically"})
items = d.get("items") or []
print(f"_api /toolkits -> {len(items)} item | tipe={type(d).__name__} kunci={list(d)[:6]}")

cat = mod.fetch_catalog(key)
print(f"fetch_catalog -> {len(cat)} toolkit | contoh: {[c['slug'] for c in cat[:5]]}")

print("fetch_connections(aris):", len(mod.fetch_connections(key, "aris")))

PG = "pg-test-7324985c-df52-4b78-9742-edc068326f14"
conns = mod.fetch_connections(key, PG)
print(f"fetch_connections({PG[:12]}…) -> {len(conns)} koneksi: {[(c['slug'], c['status']) for c in conns]}")

# apa yang panel TAMPILKAN kalau user_id diganti ke pg-test
specs = mod.cards(key, PG, show_all=True)
gmail = [s for s in specs if "gmail" in str(s.get("id", "")).lower()]
print(f"cards(user=pg-test) -> {len(specs)} kartu | Gmail: {[(s.get('id'), s.get('status'), s.get('status_text')) for s in gmail]}")
