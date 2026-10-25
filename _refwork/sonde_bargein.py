# -*- coding: utf-8 -*-
"""Probe Fase 4 barge-in — apa yang hilang saat pengguna memotong JARVIS.

MERAH sebelum implementasi (harus gagal terhadap kode SEKARANG):
  1. core.bargein belum ada (cut_tail, compose_cut_note).
  2. main.py belum menyimpan _cut_note, belum menggilirkan _flush_cut_note,
     belum menyuntik catatan dengan turn_complete=False.
  3. core/prompt.txt belum mendefinisikan [PUTUSAN_TADI].

Yang di-mock: TIDAK ADA. Fungsi murni core.bargein diuji langsung;
cek struktur membaca berkas asli.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")
PROMPT_SRC = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
PROMPT_FLAT = " ".join(PROMPT_SRC.split())

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


# ── 1. fungsi murni core.bargein ─────────────────────────────────────────────
try:
    from core.bargein import (CUT_TAG, CUT_TAIL_MAX, compose_cut_note,
                              cut_tail)
except Exception as e:                                   # noqa: BLE001
    print(f"  import core.bargein: {type(e).__name__}: {e} => FAIL")
    errs.append("import core.bargein")
else:
    check("ekor dipotong pada batas",
          len(cut_tail("kata " * 2000)) <= CUT_TAIL_MAX,
          f"len={len(cut_tail('kata ' * 2000))} batas={CUT_TAIL_MAX}")
    check("ekor mengambil bagian AKHIR",
          cut_tail("awal tengah AKHIR").endswith("AKHIR"))
    check("ekor dari kosong tetap kosong", cut_tail(None) == ""
          and cut_tail("   ") == "")

    check("tak ada fakta -> tak ada catatan", compose_cut_note("", "") == "")
    check("tak ada fakta (None) -> tak ada catatan",
          compose_cut_note(None, None) == "")

    _n = compose_cut_note("jadi totalnya dua ratus ribu rupiah", "")
    check("catatan bertagar", _n.startswith(CUT_TAG), _n[:40])
    check("catatan memuat ekor ucapan", "dua ratus ribu" in _n)
    check("catatan melarang mengulang", "ulang" in _n)

    _n2 = compose_cut_note("", "tunggu maksudku yang kemarin")
    check("catatan memuat ucapan pemotong", "yang kemarin" in _n2)
    check("ucapan pemotong dinormalkan",
          "baris satu baris dua" in compose_cut_note("", "baris satu\nbaris dua"))

    _n3 = compose_cut_note("ekor", "x" * 500)
    check("ucapan pemotong dibatasi 200",
          "x" * 200 in _n3 and "x" * 201 not in _n3, f"panjang partial terlihat di catatan")

    check("ekor panjang dipangkas di dalam catatan",
          len(compose_cut_note("kata " * 2000, "potong")) <= CUT_TAIL_MAX + 300)


# ── 2. main.py — simpan, putar sekali, suntik aman ───────────────────────────
check("main: compose_cut_note dipakai", "compose_cut_note(" in MAIN_SRC)
check("main: catatan sekali pakai disimpan", "self._cut_note" in MAIN_SRC)
check("main: penggilir catatan ada", "def _flush_cut_note" in MAIN_SRC)
check("main: catatan disuntik tanpa menutup giliran",
      "await self._flush_cut_note()" in MAIN_SRC)
check("main: catatan disuntik DUA titik (interupsi + ucapan baru)",
      MAIN_SRC.count("await self._flush_cut_note()") >= 2,
      f"x{MAIN_SRC.count('await self._flush_cut_note()')}")
_i_turn = MAIN_SRC.find("await self._flush_cut_note()")
_i_utt  = MAIN_SRC.find("self._utt_open = True")
check("main: catatan sesudah penanda ucapan baru",
      _i_turn != -1 and _i_utt != -1)
check("main: catatan dibuang setelah terpakai",
      "self._cut_note = \"\"" in MAIN_SRC)
check("main: jalur interupsi menyimpan sebelum continue",
      "self._cut_note = " in MAIN_SRC and "continue" in MAIN_SRC)
check("main: pakai flag bargein_note",
      '"voice.bargein_note"' in MAIN_SRC and "_flag(" in MAIN_SRC)


# ── 3. prompt mendefinisikan tag ─────────────────────────────────────────────
check("prompt: tag putusan tadi", "[PUTUSAN_TADI]" in PROMPT_FLAT)

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
