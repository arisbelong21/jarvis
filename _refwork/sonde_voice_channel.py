"""Sonde: otak harus tahu bahwa ia MENDENGAR, bukan membaca teks.

Gejala 23 Okt: transkrip suara masuk benar, tetapi otak menjawab
"Tidak. Aku hanya baca pesan teksmu" — model menyimpulkan dirinya buta-suara
karena konteks tidak menyebut kanal input.

Bukti perbaikan (probe_hearing_denial.py, 3 ucapan x 3 ulangan):
  tanpa penanda : 4/9 menjawab wajar
  dengan penanda: 9/9 menjawab wajar

Sonde ini menjaga agar penanda itu tidak hilang dan tetap murah.
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


note = getattr(M, "_VOICE_CHANNEL_NOTE", "")
check("_VOICE_CHANNEL_NOTE ada", bool(note), "kosong")
if note:
    low = note.lower()
    check("menyebut MIKROFON", "mikrofon" in low)
    check("menyebut transkripsi suara", "transkripsi" in low)
    check("melarang menjawab 'hanya membaca teks'",
          "hanya membaca teks" in low or "baca teks" in low)
    check("melarang menyuruh menyalakan mic",
          "menyalakan mikrofon" in low or "nyalakan" in low)
    check("tetap murah (<= 400 char)", len(note) <= 400, f"{len(note)} char "
          f"(~{len(note)//4} tok)")

# penanda harus BENAR-BENAR ikut ke system prompt otak
notes = getattr(M, "_brain_extra_notes", None)
check("_brain_extra_notes tersedia", callable(notes), "tidak ada")
if callable(notes):
    joined = "\n".join(notes())
    check("penanda kanal ikut di system prompt otak",
          getattr(M, "_VOICE_CHANNEL_NOTE", "") in joined)
    check("aturan tool tersembunyi tetap ikut",
          getattr(M, "_HIDDEN_TOOLS_NOTE", "") in joined)
    check("aturan bahasa ikut di system prompt otak",
          getattr(M, "_LANGUAGE_NOTE", "") in joined)
    # 24 Okt — batas dinaikkan 900 -> 1100 dengan sengaja: aturan bahasa
    # (+277 char ~ 70 tok) menutup jawaban yang keluar dalam bahasa asing
    # akibat salah transkripsi. Satu jawaban salah bahasa memaksa pengguna
    # mengulang seluruh pertanyaan (~12.000 tok), jadi 70 tok tetap murah.
    # Angka ini penjaga: kalau catatan tetap membengkak lagi, tes ini merah.
    check("total catatan tetap murah (<= 1100 char)", len(joined) <= 1100,
          f"{len(joined)} char (~{len(joined)//4} tok)")

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
