"""Sonde: otak custom tidak boleh diputus saat sedang BEKERJA, dan rotasi
model harus terlihat.

Tiga keluhan nyata (23 Okt, dari log bos):

  1. "model river tidak menjawab, padahal river tidak dipakai"
     -> FallbackLLM memang MEROTASI provider, tetapi UI tidak ikut berubah,
        dan pesan yang diucapkan menyebut "Gemini menjawab" walau yang
        menjawab adalah provider custom cadangan.

  2. "coding jalan 2-3 menit lalu dilaporkan model tidak merespon"
     -> `_BRAIN_IDLE_TIMEOUT` 120 s membunuh giliran saat satu panggilan
        provider masih berjalan (model besar memang bisa >120 s untuk satu
        langkah). Berjalan != mati.

  3. "saya cuma bisa pantau dari cmd"
     -> progres pekerjaan latar hanya dicetak ke konsol.

Yang diuji di sini: helper keputusan yang membuat ketiganya bisa diperbaiki
tanpa menebak.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M                                                # noqa: E402
from core import custom_brain as CB                              # noqa: E402

ERRORS = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        ERRORS.append(name)


# ── 1. kesabaran panggilan provider ───────────────────────────────────────
to = getattr(M, "_brain_call_timeout", None)
check("helper _brain_call_timeout tersedia", callable(to),
      "tidak ada" if to is None else "")
if callable(to):
    check("nilai config 120 tidak lagi memotong di 120",
          to(120) >= 300, f"{to(120)}")
    check("tanpa config -> minimal 300 detik", to(0) >= 300, f"{to(0)}")
    check("nilai sangat besar tetap dibatasi (600)",
          to(100000) <= 600, f"{to(100000)}")
    check("nilai negatif/rusak tidak meledak", to("abc") >= 300, f"{to('abc')}")

# ── 2. "berjalan" bukan "mati" ────────────────────────────────────────────
stalled = getattr(M, "_brain_stalled", None)
check("helper _brain_stalled tersedia", callable(stalled),
      "tidak ada" if stalled is None else "")
if callable(stalled):
    check("panggilan provider sedang berjalan -> TIDAK dianggap macet",
          stalled(now=1000.0, last_progress=100.0, in_call=True) is False)
    check("benar-benar tidak bergerak -> dianggap macet",
          stalled(now=1000.0, last_progress=100.0, in_call=False) is True)
    check("baru bergerak -> belum macet",
          stalled(now=1000.0, last_progress=990.0, in_call=False) is False)

# ── 3. rotasi provider terlihat ───────────────────────────────────────────
check("hook rotasi tersedia di custom_brain",
      hasattr(CB, "set_fallback_hook"), "tidak ada")

if hasattr(CB, "set_fallback_hook"):
    seen = []

    class _Boom:
        model = "model-rusak"

        def complete(self, messages, tools=None, timeout=120):
            raise RuntimeError("HTTP 503 service unavailable")

    class _Good:
        model = "model-bagus"

        def complete(self, messages, tools=None, timeout=120):
            return "halo", []

    CB.set_fallback_hook(lambda pid, model, err: seen.append((pid, model)))
    fb = CB.FallbackLLM([("rusak", _Boom()), ("bagus", _Good())])
    text, _ = fb.complete([{"role": "user", "content": "hai"}], None, 30)
    check("rotasi tetap menjawab", text == "halo", str(text))
    check("hook rotasi dipanggil dengan model yang BENAR",
          seen == [("bagus", "model-bagus")], str(seen))
    CB.set_fallback_hook(None)

# ── 4. penanda "sedang memanggil" pada otak ───────────────────────────────
check("CustomBrain punya penanda in_call",
      "in_call" in getattr(CB.CustomBrain, "__init__").__code__.co_names
      or hasattr(CB.CustomBrain, "in_call"),
      "belum ada")

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
