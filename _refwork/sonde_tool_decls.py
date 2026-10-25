# -*- coding: utf-8 -*-
"""Sonde deklarasi tool — blok data dipindah utuh, tidak hilang satu pun.

Langkah pertama refactor main.py: 275 baris literal dipindah ke
core/tool_decls.py. Sonde ini mengunci bahwa isinya tidak berubah (14 tool
inti, nama sama, urutan sama) dan main.py benar-benar memakai modul itu.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


HARAP = ["system_status", "screen_process", "switch_model", "switch_engine",
         "agent_run", "reload_plugins", "screen_share", "close_camera",
         "manage_monitor", "shutdown_jarvis", "save_memory", "recall_memory",
         "model_status", "undo"]

try:
    from core.tool_decls import TOOL_DECLARATIONS as TD
except Exception as e:                                   # noqa: BLE001
    print(f"  impor core.tool_decls: {type(e).__name__}: {e} => FAIL")
    raise SystemExit(1)

print("== isi modul ==")
names = [d.get("name") for d in TD if isinstance(d, dict)]
check("14 tool inti", len(TD) == 14, len(TD))
check("nama & urutan sama", names == HARAP, names[:4])
check("semua punya deskripsi",
      all(str(d.get("description") or "").strip() for d in TD))
check("semua punya parameters",
      all(isinstance(d.get("parameters"), dict) for d in TD))

print("== main.py memakai modul ==")
src = (ROOT / "main.py").read_text(encoding="utf-8")
check("main.py mengimpor dari core.tool_decls",
      "from core.tool_decls import TOOL_DECLARATIONS" in src)
check("blok literal sudah tidak ada di main.py",
      "TOOL_DECLARATIONS = [" not in src)
check("TOOL_DECLARATIONS masih dipakai (build config)",
      src.count("TOOL_DECLARATIONS") >= 2, src.count("TOOL_DECLARATIONS"))
check("switch_engine tetap terdaftar",
      any(d.get("name") == "switch_engine" for d in TD))

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
