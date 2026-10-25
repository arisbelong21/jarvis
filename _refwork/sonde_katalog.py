"""Lacak tepat di mana 200 item menjadi 0 di dalam logika fetch_catalog."""
import importlib.util

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

PLUGIN = str(ROOT / "plugins/composio_connector.py")
spec = importlib.util.spec_from_file_location("composio_connector", PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

key = "ak_L4S_h1S7YBoMG_rejL80"

data = mod._api(key, "GET", "/toolkits", params={"limit": 200, "sort_by": "alphabetically"})
items = data.get("items") or data.get("toolkits") or []
print("items diterima:", len(items))
print("contoh item (slug, deprecated):", [(it.get("slug"), it.get("deprecated")) for it in items[:8]])

# tiru loop fetch_catalog TEPAT
apps = []
for it in items:
    if not isinstance(it, dict):
        continue
    apps.append({
        "slug": str(it.get("slug") or ""),
        "name": str(it.get("name") or it.get("slug") or ""),
        "deprecated": bool(it.get("deprecated")),
    })
print("apps terkumpul:", len(apps))
print("slug kosong:", sum(1 for a in apps if not a["slug"]))
print("deprecated:", sum(1 for a in apps if a["deprecated"]))
filtered = [a for a in apps if a["slug"] and not a["deprecated"]]
print("LOLOS filter:", len(filtered), "| contoh:", [a["slug"] for a in filtered[:8]])

print("next_cursor di respons:", repr(data.get("next_cursor")), "| nextCursor:", repr(data.get("nextCursor")))
