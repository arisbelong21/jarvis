# -*- coding: utf-8 -*-
"""Probe Fase 4 Kandidat C — gerbang diam staySilentPlan.

MERAH sebelum implementasi (harus gagal terhadap kode SEKARANG):
  1. core.silence belum ada (fungsi murni stay_silent_plan).
  2. main.py belum memasang gerbang di jalur bicara proaktif, belum
     menghitung "sudah bicara", belum membaca flag voice.silence_gate.

Yang di-mock: TIDAK ADA. stay_silent_plan diuji sebagai fungsi NYATA —
termasuk SELURUH 16 kombinasi masukan, karena sonde ini ada justru untuk
mengunci urutan empat kondisinya.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


# ── 1. fungsi murni: 16 kombinasi, urutan empat kondisi ─────────────────────
try:
    from core.silence import stay_silent_plan
except Exception as e:                                   # noqa: BLE001
    print(f"  import stay_silent_plan: {type(e).__name__}: {e} => FAIL")
    errs.append("import stay_silent_plan")
else:
    # Urutan terdokumentasi (ADAPTASI-GROK.md §Kandidat C):
    #   mereka baru bicara     → WAJIB bicara
    #   sudah bicara siklus ini → BOLEH diam
    #   ada hasil belum disampaikan → WAJIB bicara
    #   ada tanda terima belum diucapkan → WAJIB bicara
    #   selain itu             → BOLEH diam
    _kasus = [
        # (theyJustTalked, newsOwed, receiptOwed, alreadySpoke, staySilent)
        (True,  True,  True,  True,  False),  # bicara: mengalahkan semua
        (True,  True,  True,  False, False),
        (True,  True,  False, True,  False),
        (True,  True,  False, False, False),
        (True,  False, True,  True,  False),
        (True,  False, True,  False, False),
        (True,  False, False, True,  False),
        (True,  False, False, False, False),
        (False, True,  True,  True,  True),   # sudah bicara: boleh diam
        (False, True,  True,  False, False),  # kabar tertunggak: wajib
        (False, True,  False, True,  True),
        (False, True,  False, False, False),
        (False, False, True,  True,  True),   # sudah bicara: boleh diam
        (False, False, True,  False, False),  # tanda terima: wajib
        (False, False, False, True,  True),   # diam boleh
        (False, False, False, False, True),   # diam boleh
    ]
    for _i, (_t, _n, _r, _s, _exp) in enumerate(_kasus):
        _diam, _why = stay_silent_plan(_t, _n, _r, _s)
        check(f"kombinasi {_i}: just={_t} news={_n} receipt={_r} "
              f"spoke={_s} -> stay={_exp}",
              _diam is _exp and isinstance(_why, str) and bool(_why),
              f"stay={_diam} ({_why[:40]})")

    # Tanda tangan: empat flag, bukan objek/dict — memanggil dengan kata
    # kunci harus sama hasilnya dengan posisional.
    check("panggilan kata kunci = posisional",
          stay_silent_plan(they_just_talked=True, news_owed=False,
                           receipt_owed=False, already_spoke=False)[0]
          is stay_silent_plan(True, False, False, False)[0])
    # Deterministik.
    check("deterministik (3x identik)",
          len({stay_silent_plan(False, True, False, False)
               for _ in range(3)}) == 1)

# ── 2. main.py: gerbang terpasang ───────────────────────────────────────────
check("main: stay_silent_plan diimpor/dipakai",
      "stay_silent_plan" in MAIN_SRC)
check("main: flag voice.silence_gate dibaca",
      '"voice.silence_gate"' in MAIN_SRC)
# Gerbang harus duduk di jalur bicara proaktif (sebelum mark_triggered).
_run_i = MAIN_SRC.find("async def _run_proactive_mode")
_run_seg = MAIN_SRC[_run_i:_run_i + 2600] if _run_i >= 0 else ""
check("main: gerbang ada di _run_proactive_mode",
      "stay_silent_plan" in _run_seg)
check("main: gerbang SEBELUM mark_triggered",
      _run_seg.find("stay_silent_plan") >= 0
      and _run_seg.find("mark_triggered") > _run_seg.find("stay_silent_plan"))
check("main: gerbang membaca kabar tertunggak",
      "_deferred_results" in _run_seg)
check("main: gerbang membaca tanda terima tertahan",
      "_receipt_pending" in _run_seg)
# Penanda "sudah bicara" harus benar-benar dicatat.
check("main: penanda sudah-bicara ada", "_mark_spoke" in MAIN_SRC)
check("main: plugin_say menandai bicara",
      "_mark_spoke()" in MAIN_SRC)

# ── 3. config: sakelar ───────────────────────────────────────────────────────
try:
    import config
    check("config: voice.silence_gate ada di default",
          "voice.silence_gate" in config.VOICE_FLAG_DEFAULTS)
    check("config: voice.silence_gate default ON",
          config.VOICE_FLAG_DEFAULTS.get("voice.silence_gate") is True)
    check("config: get_flags membacanya",
          config.get_flags().get("voice.silence_gate") is True)
except Exception as e:                                   # noqa: BLE001
    check("config: voice.silence_gate", False, f"{type(e).__name__}: {e}")

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
