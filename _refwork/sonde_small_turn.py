"""Sonde: ucapan sepele tidak boleh dibayar 12.000 token.

Kejadian 24 Okt: kuota Claude habis oleh percakapan biasa. Sebabnya setiap
ucapan — termasuk "oke" dan "habis limitnya" — dikirim ke otak sebagai satu
panggilan penuh (~11.900 token: system 4.900 + tool 7.000).

Aturan yang diuji:
  - `small_talk` / `farewell`  -> bukan tugas, jawab mulut sendiri
  - `unclear` pendek tanpa tanya -> komentar, jawab mulut sendiri
  - `oke/siap/lanjut/ya`       -> JANGAN dianggap obrolan (itu perintah implisit)
  - pertanyaan / kalimat panjang -> tetap ke otak
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


small = getattr(M, "_is_small_turn", None)
check("_is_small_turn ada", callable(small), "tidak ada")

if callable(small):
    print("\n== obrolan: TIDAK perlu otak ==")
    for u in ("berkah semua gitu loh kan.", "habis limitnya",
              "halo jarvis", "makasih ya", "wkwk", "bagus banget"):
        check(f"obrolan: {u[:28]!r}", small(u) is True, str(small(u)))

    print("\n== perintah implisit: HARUS tetap ke otak ==")
    for u in ("oke", "ya", "siap lanjut", "lanjutkan", "gas"):
        check(f"perintah: {u[:28]!r}", small(u) is False, str(small(u)))

    print("\n== pertanyaan & tugas: HARUS tetap ke otak ==")
    for u in ("model apa yang digunakan sekarang?",
              "oke sekarang kenapa kamu bilang bahwa CloudOPS 5.5 tadi itu aman?",
              "buatkan file laporan.txt di desktop",
              "cek jadwal saya hari ini"):
        check(f"bukan obrolan: {u[:30]!r}", small(u) is False, str(small(u)))

SRC = (ROOT / "main.py").read_text(encoding="utf-8")
check("gerbang dipasang di dispatch terpusat",
      "small_turn=_is_small_turn(_utt)" in SRC)
check("dispatch menjalankan keputusan SMALL",
      'if _dec["action"] == DEC_SMALL:' in SRC)
check("gerbang tidak aktif saat mode paksa",
      "_force_brain" in SRC and "_is_small_turn" in SRC)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
