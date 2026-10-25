# -*- coding: utf-8 -*-
"""Sonde bicara — kontrak prompt mulut SETELAH routing A/B/C (23 Okt).

Riwayat:
  - v1: prompt lama mewajibkan kalimat pembuka sebelum `brain` (dihapus,
    karena pengguna mengira ada pekerjaan yang tidak diminta).
  - v2: prompt "SEMUA ucapan wajib lewat brain" — dihapus juga, karena
    perintah kecil (buka youtube, cek jadwal) jadi lambat.
  - v3 (sekarang): tiga jalur — AKSI -> brain, KECIL -> tool langsung,
    OBROLAN -> jawab sendiri.

Sonde ini menjaga agar aturan itu tidak hilang dan penjaga lain tetap utuh.
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


SRC = (ROOT / "main.py").read_text(encoding="utf-8")

print("== routing tiga jalur ==")
check("jalur A (aksi -> brain) ada", "A. AKSI / KERJA" in SRC)
check("jalur B (perintah kecil -> tool langsung) ada",
      "B. PERINTAH KECIL / CEPAT" in SRC)
check("jalur C (obrolan -> jawab sendiri) ada", "C. OBROLAN" in SRC)
check("aturan 'ragu -> pilih A' ada", "Ragu antara A dan B? Pilih A." in SRC)
check("aturan lama 'SEMUA ucapan wajib lewat brain' sudah tidak ada",
      "SEMUA ucapan pengguna WAJIB melewati tool ini" not in SRC)

print("== mulut tidak bicara sebelum otak bekerja ==")
check("JANGAN ucapkan apa pun sebelum memanggil brain",
      "JANGAN mengucapkan apa pun sebelum memanggil `brain`" in SRC)
check("contoh kalimat lama 'Siap, saya kerjakan' tidak ada lagi",
      "Siap, saya kerjakan" not in SRC)
check("instruksi dijalankan, bukan dibaca mentah",
      "KECUALI bila jawaban itu berbentuk INSTRUKSI" in SRC)

print("== aturan lain tetap utuh ==")
for frasa in ("Ucapkan jawaban `brain` kata demi kata",
              "Kalau jawaban berisi pertanyaan",
              "Jangan mengarang hasil"):
    check(f"aturan '{frasa[:26]}…' utuh", frasa in SRC)

print("== penjaga saat otak sibuk ==")
check("tool lain ditahan saat otak bekerja",
      "Otak custom masih mengerjakan permintaan ini" in SRC)
check("brain menolak giliran kedua saat masih sibuk",
      'if getattr(self, "_in_brain", False):' in SRC
      and 'masih mengerjakan permintaan "' in SRC)
check("kalimat sibuk meminta mulut menjawab singkat",
      "sebentar lagi. Jangan memanggil tool" in SRC)

print("== lokasi tambahan (tidak menyebar) ==")
i = SRC.index("def _voice_interface_prompt")
blok = SRC[i:i + 2600]
check("routing ada di dalam fungsi prompt mulut", "A. AKSI / KERJA" in blok)
check("hanya satu kemunculan routing A di seluruh main.py",
      SRC.count("A. AKSI / KERJA") == 1, SRC.count("A. AKSI / KERJA"))

print("== sakelar mode langsung berlaku ==")
_i = SRC.index('elif name == "switch_engine"')
_blok = SRC[_i:_i + 1800]
check("switch_engine memanggil request_reconnect", "request_reconnect" in _blok)
check("reconnect diberi jeda agar kalimat terucap", "call_later" in _blok)

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
