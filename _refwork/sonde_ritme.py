# -*- coding: utf-8 -*-
"""Probe Fase 4 Slice 5 — jendela sunyi 30 detik (anti-spam).

MERAH sebelum implementasi (harus gagal terhadap kode SEKARANG):
  1. core.receipt belum punya deferred_block / DEFERRED_TAG / DEFERRED_MAX.
  2. main.py belum menahan hasil "done" yang mendarat saat pengguna sudah
     diam (tidak ada _deferred_results, _flush_deferred_results,
     _quiet_window_passed, flag voice.quiet_window).
  3. core/prompt.txt belum mendefinisikan [HASIL_TUNDA].

Yang di-mock: TIDAK ADA. deferred_block diuji sebagai fungsi NYATA
core.receipt; cek struktur membaca berkas asli.
"""
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


# ── 1. fungsi murni deferred_block ────────────────────────────────────────────
try:
    from core.receipt import (DEFERRED_MAX, DEFERRED_TAG, deferred_block)
except Exception as e:                                   # noqa: BLE001
    print(f"  import deferred_block: {type(e).__name__}: {e} => FAIL")
    errs.append("import deferred_block")
else:
    check("tak ada hasil -> tak ada blok", deferred_block([]) == "")
    check("None -> tak ada blok", deferred_block(None) == "")
    _b = deferred_block(["✓ email terkirim", "✗ smtp menolak"])
    check("blok bertagar", _b.startswith(DEFERRED_TAG), _b[:30])
    check("blok memuat semua baris",
          "email terkirim" in _b and "smtp menolak" in _b)
    _b2 = deferred_block(["✓ ada", "   ", ""])
    check("baris kosong dibuang",
          _b2.count("\n") == 2 and _b2.count("✓ ada") == 1,
          f"{_b2!r}")
    check("isi bukan tag", DEFERRED_TAG in ("[HASIL_TUNDA]",))

    # batas jumlah baris diterapkan oleh pemanggil; modul memastikan bentuk.
    check("DEFERRED_MAX wajar", 3 <= DEFERRED_MAX <= 10,
          f"DEFERRED_MAX={DEFERRED_MAX}")

# ── 2. main.py — tahan, batasi, sampaikan sekali ─────────────────────────────
check("main: deferred_block dipakai", "deferred_block(" in MAIN_SRC)
check("main: penampung hasil ditahan", "self._deferred_results" in MAIN_SRC)
check("main: penyampai hasil tunda ada", "def _flush_deferred_results" in MAIN_SRC)
check("main: flag quiet window dipakai",
      '"voice.quiet_window"' in MAIN_SRC and "_flag(" in MAIN_SRC)
check("main: jendela 30 detik disebut",
      "QUIET_WINDOW_SECONDS = 30" in MAIN_SRC
      and "def _quiet_window_passed" in MAIN_SRC)
check("main: hasil ditahan dibatasi",
      "DEFERRED_MAX" in MAIN_SRC)

# Kegagalan/konfirmasi TIDAK boleh ikut ditahan: harus tetap langsung bicara.
_on_done = MAIN_SRC[MAIN_SRC.find("async def _on_job_done")
                    if "async def _on_job_done" in MAIN_SRC
                    else MAIN_SRC.find("def _on_job_done"):]
_on_done = _on_done[:_on_done.find("def ", 10)] if _on_done else ""
check("main: kegagalan tak pernah ditahan",
      bool(_on_done) and 'verdict == "done"' in _on_done
      and "failed" in _on_done)
check("main: hasil kosong tak diumumkan",
      bool(_on_done) and '"Done."' in _on_done)

# ── 3. prompt mendefinisikan tag ─────────────────────────────────────────────
check("prompt: tag hasil tunda", "[HASIL_TUNDA]" in PROMPT_FLAT)

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
