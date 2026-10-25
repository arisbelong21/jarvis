"""Sonde: memori harus bisa dipakai MULUT (Gemini), termasuk di mode curhat.

Dijalankan SEBELUM perbaikan -> harus MERAH.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M
from core.decide import decide_turn
from core.tool_decls import TOOL_DECLARATIONS

ERRORS: list[str] = []
OK = 0


def check(name: str, got, want) -> None:
    global OK
    if got == want:
        OK += 1
        print(f"  OK   {name}")
    else:
        ERRORS.append(name)
        print(f"  FAIL {name}: dapat {got!r}, harusnya {want!r}")


print("== 1. tool memori dipegang mulut ==")
check("save_memory ada di _VOICE_TOOLS", "save_memory" in M._VOICE_TOOLS, True)
check("recall_memory ada di _VOICE_TOOLS", "recall_memory" in M._VOICE_TOOLS, True)

_decl_names = {d.get("name") for d in M._voice_tool_decls(TOOL_DECLARATIONS)}
check("deklarasi save_memory sampai ke Gemini", "save_memory" in _decl_names, True)
check("deklarasi recall_memory sampai ke Gemini", "recall_memory" in _decl_names, True)
check("tool berat tetap TIDAK dipegang Gemini",
      bool({"send_message", "dev_agent", "code_helper", "run_command"}
           & _decl_names), False)

print("\n== 2. mode curhat tidak mengalihkan tool memori ke otak ==")
for nm in ("save_memory", "recall_memory"):
    d = decide_turn("simpan cerita saya", name=nm, vent_mode=True)
    check(f"{nm} di mode curhat -> pass", d["action"], "pass")
d = decide_turn("simpan cerita saya", name="save_memory")
check("save_memory di mode normal -> pass", d["action"], "pass")
d = decide_turn("buatkan laporan", name="save_memory", is_builtin=True,
                write_intent=True, gemini_allowed=False)
check("save_memory tidak pernah dialihkan walau niat menulis",
      d["action"], "pass")

print("\n== 3. prompt mulut mengajari memori ==")
p = M._voice_interface_prompt("JARVIS")
check("prompt menyebut recall_memory", "recall_memory" in p, True)
check("prompt menyebut save_memory", "save_memory" in p, True)
check("prompt melarang mencari memori di web/layar",
      "JANGAN mencari" in p or "jangan mencari" in p, True)
check("prompt menyebut mode curhat tetap boleh menyimpan",
      "mode curhat" in p.lower(), True)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
