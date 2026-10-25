"""Sonde: abort 1008, backoff reconnect, izin computer_control per aksi,
reload tanpa halaman, dan penguncian bahasa mulut.

Dijalankan SEBELUM perbaikan -> harus MERAH.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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


class _FakeAPIError(Exception):
    """Tiruan google.genai.errors.APIError: punya status_code WebSocket."""

    def __init__(self, code: int, msg: str = "The operation was aborted."):
        super().__init__(f"{code} None. {msg}")
        self.status_code = code


print("== 1. _is_conn_closed mengenali APIError 1008 ==")
try:
    import main as M
except Exception as e:                                   # noqa: BLE001
    print("  FATAL impor main:", e)
    sys.exit(2)

check("APIError 1008 = koneksi tertutup", M._is_conn_closed(_FakeAPIError(1008)), True)
check("APIError 1011 = koneksi tertutup", M._is_conn_closed(_FakeAPIError(1011)), True)
check("ValueError biasa bukan koneksi tertutup",
      M._is_conn_closed(ValueError("x")), False)
check("TimeoutError tetap dikenali", M._is_conn_closed(TimeoutError()), True)

print("\n== 2. backoff untuk abort beruntun ==")
check("belum ada abort -> tanpa jeda", M._abort_backoff(0), 0.0)
check("abort pertama -> tanpa jeda", M._abort_backoff(1), 0.0)
check("abort kedua -> 2 detik", M._abort_backoff(2), 2.0)
check("abort ketiga -> 4 detik", M._abort_backoff(3), 4.0)
check("abort keempat -> 8 detik", M._abort_backoff(4), 8.0)
check("tidak pernah melebihi 8 detik", M._abort_backoff(9), 8.0)

print("\n== 3. izin computer_control per AKSI, bukan per tool ==")
from core.agent import permissions as P

check("screenshot -> allow",
      P.decide("computer_control", {}, args={"action": "screenshot"})[0], "allow")
check("wait -> allow (inilah yang memblokir otak di log)",
      P.decide("computer_control", {}, args={"action": "wait"})[0], "allow")
check("scroll -> allow",
      P.decide("computer_control", {}, args={"action": "scroll"})[0], "allow")
check("move -> allow",
      P.decide("computer_control", {}, args={"action": "move"})[0], "allow")
check("type -> confirm (dari Gemini)",
      P.decide("computer_control", {}, args={"action": "type"})[0], "confirm")
check("paste -> confirm",
      P.decide("computer_control", {}, args={"action": "paste"})[0], "confirm")
check("type dari OTAK internal -> allow (jalur teliti)",
      P.decide("computer_control", {}, args={"action": "type"},
               internal=True)[0], "allow")
check("deny tetap menang atas internal",
      P.decide("computer_control",
               {"tool_permissions": {"deny": ["computer_control"]}},
               args={"action": "wait"}, internal=True)[0], "deny")
check("screen_agent tetap confirm (bukan computer_control)",
      P.decide("screen_agent", {}, args={})[0], "confirm")

print("\n== 4. reload tidak boleh meluncurkan browser ==")
SRC_B = (ROOT / "actions" / "browser_control.py").read_text(encoding="utf-8")
check("ada helper _active_page", "_active_page" in SRC_B, True)
_m = re.search(r"async def reload\(self\)[^\n]*\n(.*?)\n    async def ", SRC_B, re.S)
check("reload memakai _active_page", bool(_m) and "_active_page" in _m.group(1), True)
check("reload TIDAK memanggil _get_page",
      bool(_m) and "_get_page" not in _m.group(1), True)

print("\n== 5. bahasa mulut dikunci ke Indonesia ==")
SRC = (ROOT / "main.py").read_text(encoding="utf-8")
check("ada aturan bahasa", "_LANGUAGE_NOTE" in SRC, True)
check("aturan menyebut Bahasa Indonesia", "Bahasa Indonesia" in SRC, True)
check("aturan dipakai di prompt mulut",
      SRC.count("_LANGUAGE_NOTE") >= 2, True)

print("\n== 6. sambungan: gate menerima internal ==")
check("main mengirim internal ke gate",
      "internal=bool(_internal)" in SRC, True)
check("gate meneruskan internal",
      "internal" in (ROOT / "core" / "decision_loop.py").read_text(encoding="utf-8"), True)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
