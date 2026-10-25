"""Sonde: nilai memori cukup panjang untuk cerita, tetapi prompt tetap murah,
dan konteks memori sudah dimuat sejak sesi berjalan.

Dijalankan SEBELUM perbaikan -> harus MERAH.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memory import memory_manager as mm

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


print("== 1. batas nilai cukup untuk cerita ==")
check("MAX_VALUE_LENGTH >= 1000", mm.MAX_VALUE_LENGTH >= 1000, True)
_long = "kata " * 600                      # 3.000 char
_out = mm._truncate_value(_long)
check("nilai 3.000 char tidak dipotong jadi 380", len(_out) > 1000, True)
check("tetap dibatasi (tidak tanpa batas)",
      len(_out) <= mm.MAX_VALUE_LENGTH + 1, True)

print("\n== 2. prompt tetap murah walau nilai panjang ==")
_mem = {
    "identity": {"name": {"value": "Aris"}},
    "notes": {"curhat_panjang": {"value": _long, "updated": "2026-10-25"}},
}
_block = mm.format_memory_for_prompt(_mem)
_line = [l for l in _block.splitlines() if "curhat" in l.lower()]
check("baris fakta panjang ada", bool(_line), True)
check("satu fakta tidak mendominasi prompt (<= 260 char)",
      bool(_line) and len(_line[0]) <= 260, True)
check("prompt total tetap wajar (<= 1600 char)", len(_block) <= 1600, True)

print("\n== 3. recall mengembalikan nilai lebih utuh ==")
tmp = Path(tempfile.mkdtemp()) / "long_term.json"
tmp.write_text(json.dumps({
    "identity": {}, "notes": {"cerita": {"value": _long, "updated": "2026-10-25"}},
}, ensure_ascii=False), encoding="utf-8")
_orig = mm.MEMORY_PATH
mm.MEMORY_PATH = tmp
_found = mm.search_memory("kata")
mm.MEMORY_PATH = _orig
check("recall mengembalikan lebih dari 380 char", len(_found) > 500, True)
check("recall mengembalikan nilai UTUH (bukan dipotong lagi)",
      len(_found) >= mm.MAX_VALUE_LENGTH, True)

print("\n== 4. konteks memori dimuat sejak sesi berjalan ==")
_src = (ROOT / "main.py").read_text(encoding="utf-8")
_brain_branch = _src.split("if self._brain_mode:")[1].split("else:")[0]
check("prompt mulut menyertakan blok memori",
      "mem_str" in _brain_branch, True)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
