"""Probe LIVE composio_connector: meniru persis jalur panel Connections.

load plugin asli -> cards(key, "aris") -> tunggu refresh background ->
laporan: jumlah kartu, app tersambung, status kartu Gmail, state internal.
"""
import importlib.util
import json
import time

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

PLUGIN = str(ROOT / "plugins/composio_connector.py")
CONFIG = str(ROOT / "config/api_keys.json")

spec = importlib.util.spec_from_file_location("composio_connector", PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

cfg = json.load(open(CONFIG, encoding="utf-8"))
key = str((cfg.get("plugin_config") or {}).get("composio", {}).get("api_key") or "").strip()
user = str((cfg.get("plugin_config") or {}).get("composio", {}).get("user_id") or "aris")
print(f"key: {key[:8]}…  user: {user}  show_all: {cfg['plugin_config']['composio'].get('show_all_apps')}")

t0 = time.time()
specs = mod.cards(key, user)          # <= persis yang dipanggil _composio_specs
print(f"[{time.time()-t0:5.1f}s] kartu awal: {len(specs)}")
for s in specs[:6]:
    print("   ", s.get("id"), "|", s.get("status"), "|", s.get("status_text") or s.get("desc", "")[:60])

# tunggu background refresh selesai (katalog paged bisa >10s)
for i in range(90):
    st = mod.state()
    if not st.get("busy"):
        break
    time.sleep(1)
print(f"[{time.time()-t0:5.1f}s] state: {mod.state()}")

specs = mod.cards(key, user)
print(f"total kartu setelah refresh: {len(specs)}")
conn = [s for s in specs if s.get("status") == "connected" and s.get("slug")]
print(f"app TERSAMBUNG: {len(conn)} -> {[s.get('slug') for s in conn][:20]}")
gmail = [s for s in specs if "gmail" in str(s.get("id", "")).lower()]
print(f"kartu Gmail: {[(s.get('id'), s.get('status'), s.get('status_text')) for s in gmail]}")
cal = [s for s in specs if "calendar" in str(s.get("id", "")).lower()]
print(f"kartu Calendar: {[(s.get('id'), s.get('status'), s.get('status_text')) for s in cal][:5]}")
error_card = [s for s in specs if s.get("id", "").endswith("__error__")]
print(f"kartu ERROR: {error_card}")
