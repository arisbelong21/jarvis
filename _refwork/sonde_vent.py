"""Sonde: MODE CURHAT — Gemini menjawab penuh, otak tidak dipanggil.

Permintaan bos (24 Okt): saat bercerita, tidak boleh ada kata di cerita yang
memicu pengalihan ke otak. Solusinya bukan menambah pengecualian kata, tapi
MODE EKSPLISIT yang dinyalakan pengguna:

  "mode curhat"        -> semua ucapan dijawab Gemini, otak tidak dipanggil
  "mode default"       -> kembali normal (aksi ke otak, ringan ke Gemini)
  "matikan mode curhat" -> kembali normal

Yang diuji:
  A. `_vent_command()` membaca perintah on/off, termasuk negasi.
  B. `decide_turn(vent_mode=True)` -> SMALL untuk brain, PASS untuk tool ringan.
  C. Perintah kendali tetap menang (bisa keluar dari mode curhat).
  D. Sambungan nyata: flag diinisialisasi, hook terpasang, "mode default"
     mematikan mode curhat.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M                                                # noqa: E402
from core.decide import decide_turn, SMALL, PASS, MODE, BRAIN    # noqa: E402

ERRORS = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        ERRORS.append(name)


# ── A. perintah mode curhat ───────────────────────────────────────────────
vc = getattr(M, "_vent_command", None)
check("_vent_command ada", callable(vc), "tidak ada")
if callable(vc):
    for t in ("mode curhat", "aktifkan mode curhat", "kita curhat mode ya",
              "masuk mode cerita"):
        check(f"-> on: {t[:28]!r}", vc(t) == "on", str(vc(t)))
    for t in ("matikan mode curhat", "keluar dari mode curhat",
              "mode default", "stop mode curhat"):
        check(f"-> off: {t[:28]!r}", vc(t) == "off", str(vc(t)))
    for t in ("buka youtube", "mantan saya nelpon", "jangan mode curhat"):
        check(f"-> None: {t[:28]!r}", vc(t) is None, str(vc(t)))

# ── B. keputusan saat mode curhat aktif ───────────────────────────────────
check("curhat + permintaan ke otak -> dijawab mulut",
      decide_turn("buatkan laporan", name="brain", vent_mode=True)["action"],
      SMALL)
check("curhat + cerita -> dijawab mulut",
      decide_turn("dia buat saya sakit hati", name="brain",
                  vent_mode=True)["action"], SMALL)
check("curhat + tool ringan -> tetap boleh",
      decide_turn("buka youtube", name="youtube_video",
                  vent_mode=True)["action"], PASS)
check("curhat tidak mematikan perintah kendali",
      decide_turn("mode default", name="brain", vent_mode=True,
                  mode_command="default")["action"], MODE)

# ── C. tanpa mode curhat, perilaku lama ───────────────────────────────────
check("tanpa curhat: permintaan ke otak tetap ke otak",
      decide_turn("buatkan laporan", name="brain")["action"], BRAIN)

# ── D. sambungan nyata ────────────────────────────────────────────────────
SRC = (ROOT / "main.py").read_text(encoding="utf-8")
check("flag _vent_mode diinisialisasi", "self._vent_mode" in SRC)
check("hook mode curhat dipasang di transkrip", "_vent_command(" in SRC)
check("'mode default' mematikan mode curhat",
      "_vent_mode = False" in SRC)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
