"""Verifikasi pencarian lintas-katalog + regresi perilaku lama."""
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
key, user, favs = cfg["api_key"], cfg["user_id"], cfg.get("favourite_apps")

errs = []
def check(name, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        errs.append(name)

# pastikan katalog sudah ter-cache dulu (background refresh)
mod.cards(key, user, show_all=False, favs=favs)
for _ in range(60):
    if not mod.state().get("busy"):
        break
    time.sleep(1)

# 1. pencarian "sheet" — googlesheets BUKAN favorit & di luar 400 teratas
res = mod.cards(key, user, show_all=False, favs=favs, search="sheet")
slugs = [s["slug"] for s in res if s.get("slug")]
check("cari 'sheet' menemukan googlesheets (bukan favorit, di luar 400)",
      "googlesheets" in slugs, f"{len(slugs)} hasil: {slugs[:6]}")
check("cari 'sheet' juga menemukan gigasheet", "gigasheet" in slugs, slugs[:8])

# 2. pencarian favorit tetap jalan
res2 = mod.cards(key, user, show_all=False, favs=favs, search="slack")
slugs2 = [s["slug"] for s in res2 if s.get("slug")]
check("cari 'slack' menemukan slack", "slack" in slugs2, slugs2[:5])

# 3. pencarian tidak menyelipkan kartu '…lainnya'
check("mode pencarian tanpa kartu 'lainnya'",
      all(s.get("slug") for s in res))

# 4. pencarian kosong = tampilan terkurasi seperti biasa
res3 = mod.cards(key, user, show_all=False, favs=favs, search="")
check("pencarian kosong -> tampilan terkurasi",
      len([s for s in res3 if s.get("slug")]) >= 35)

# 5. pencarian app tersambung tetap ditemui & bertanda aktif
res4 = mod.cards(key, user, show_all=False, favs=favs, search="gmail")
gm = [s for s in res4 if s.get("slug") == "gmail"]
check("cari 'gmail' -> gmail ketemu & tersambung",
      len(gm) == 1 and gm[0]["status"] == "connected")

# 6. plafon hasil pencarian
res5 = mod.cards(key, user, show_all=True, favs=favs, search="a")
check("hasil pencarian dibatasi 100", len([s for s in res5 if s.get("slug")]) <= 100,
      f"{len([s for s in res5 if s.get('slug')])} hasil untuk 'a'")

print("\nERRORS:", "none" if not errs else errs)
