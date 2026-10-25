"""Verifikasi perbaikan _fill_grid tanpa PyQt6:
simulasi algoritma row/col baru dengan spec NYATA (plugin + MCP + 402 kartu
Composio live) -> assert tidak ada sel grid yang ditempati dua widget.
"""
import importlib.util
import json

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

# ── spec nyata: komposit seperti _connector_specs() ─────────────────────────
plugin_specs = [
    {"kind": "plugin", "id": "calendar", "name": "Calendar"},
    {"kind": "plugin", "id": "cloud", "name": "Cloud"},
    {"kind": "plugin", "id": "composio", "name": "Composio"},
    {"kind": "plugin", "id": "home", "name": "Home"},
    {"kind": "plugin", "id": "meeting", "name": "Meeting"},
    {"kind": "plugin", "id": "telegram", "name": "Telegram"},
]
mcp_specs = [{"kind": "mcp", "id": "rea", "name": "REA", "status_text": "✓ 207 tool"}]

PLUGIN = str(ROOT / "plugins/composio_connector.py")
spec = importlib.util.spec_from_file_location("composio_connector", PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
cfg = json.load(open(str(ROOT / "config/api_keys.json"), encoding="utf-8"))["plugin_config"]["composio"]
composio_specs = mod.cards(cfg["api_key"], cfg["user_id"])
for _ in range(90):
    if not mod.state().get("busy"):
        break
    import time; time.sleep(1)
composio_specs = mod.cards(cfg["api_key"], cfg["user_id"])
print(f"spec: {len(plugin_specs)} plugin + {len(mcp_specs)} mcp + {len(composio_specs)} composio")

specs = plugin_specs + mcp_specs + composio_specs

# ── simulasi algoritma BARU (salinan _fill_grid tanpa Qt) ────────────────────
COLS = 3
cells = {}          # (row, col) -> widget label; dua widget di satu sel = BUG
def put(r, c, label):
    key = (r, c)
    assert key not in cells, f"TUMPANG-TINDIH di {key}: {cells[key]} vs {label}"
    cells[key] = label

row, col, last_group, row_dirty = 0, 0, None, False
header_rows = []
for s in specs:
    group = s.get("group")
    if group != last_group:
        last_group = group
        if group:
            if row_dirty:
                row += 1
                row_dirty = False
            put(row, 0, f"HEADER:{group}")
            header_rows.append(row)
            row += 1
        col = 0
    put(row, col, f"card:{s.get('id')}")
    col += 1
    row_dirty = True
    if col >= COLS:
        col = 0
        row += 1
        row_dirty = False

# header harus sendirian di barisnya (tidak ada kartu di baris yang sama)
names = list(cells)
for hr in header_rows:
    same_row = [k for k in names if k[0] == hr and not cells[k].startswith("HEADER:")]
    assert not same_row, f"header baris {hr} sepapan dengan {same_row}"

print(f"OK: {len(cells)} widget, 0 tumpang tindih; header grup di baris {header_rows}")
# tunjukkan susunan 3 baris pertama agar terbukti berubah
for r in range(min(4, row + 1)):
    line = []
    for c in range(COLS):
        v = cells.get((r, c), "")
        line.append((v[:28] + "…") if len(v) > 29 else v)
    print(f"  row {r}: {line}")

# bandingkan dengan algoritma LAMA (untuk membuktikan bugnya memang ada)
cells_old = {}
def put_old(r, c, label):
    key = (r, c)
    if key in cells_old:
        return False
    cells_old[key] = label
    return True
row = col = 0; last_group = None; overlaps = 0
for s in specs:
    group = s.get("group")
    if group != last_group:
        col = 0; last_group = group
        if group:
            if not put_old(row, 0, f"HEADER:{group}"):
                overlaps += 1
            row += 1
    if not put_old(row, col, f"card:{s.get('id')}"):
        overlaps += 1
    col += 1
    if col >= COLS:
        col = 0; row += 1
print(f"algoritma LAMA: {overlaps} tumpang-tindih (inilah sumber tampilan ngebug)")
