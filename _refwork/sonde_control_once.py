"""Sonde: perintah kendali tidak boleh diproses berulang (loop 24 Okt).

Gejala: "masuk mode curhat" -> `🫂 Mode curhat AKTIF` berulang 8x.

Sebab: `plugin_say()` menyuntik kalimat ke sesi Live, yang menghasilkan
`turn_complete` BARU. Hook mode curhat membaca `_live_utterance` yang masih
berisi "masuk mode curhat", lalu memprosesnya lagi — dan berbicara lagi.

Yang diuji:
  A. `_control_once()` — teks yang sama hanya diproses sekali.
  B. Hook memakai penjaga itu (dan mengosongkan ucapan setelah ditangani).
  C. Sifat idempoten: mengaktifkan mode yang sudah aktif tidak berbicara lagi.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M                                                # noqa: E402

ERRORS = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        ERRORS.append(name)


once = getattr(M, "_control_once", None)
check("_control_once ada", callable(once), "tidak ada")
if callable(once):
    check("teks baru -> diproses", once("", "masuk mode curhat") is True)
    check("teks sama -> TIDAK diproses lagi",
          once("masuk mode curhat", "masuk mode curhat") is False)
    check("spasi berlebih tetap dianggap sama",
          once("masuk mode curhat", "  masuk   mode curhat ") is False)
    check("teks berbeda -> diproses",
          once("masuk mode curhat", "matikan mode curhat") is True)
    check("ucapan kosong -> tidak diproses", once("x", "") is False)

SRC = (ROOT / "main.py").read_text(encoding="utf-8")
check("hook mode curhat memakai penjaga",
      "_control_once(" in SRC and '"_vent_handled"' in SRC)
check("hook perintah model memakai penjaga",
      "_control_once(" in SRC and '"_switch_handled"' in SRC)
check("ucapan dikosongkan setelah perintah kendali",
      'self._vent_handled = ' in SRC and 'self._switch_handled = ' in SRC)
# 25 Okt — idempotensi pindah ke `_apply_vent_state()` (satu tempat).
check("mode curhat tidak berbicara bila status tidak berubah",
      "if bool(want) == bool(was):" in SRC)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
