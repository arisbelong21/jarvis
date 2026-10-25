# -*- coding: utf-8 -*-
"""Probe Fase 4 Slice 4 — ingat relevan OTOMATIS untuk giliran otak.

MERAH sebelum implementasi (harus gagal terhadap kode SEKARANG):
  1. core.intent belum punya _wants_recall (detektor deterministik).
  2. CustomBrain.turn belum menerima param `notes` (catatan ingatan).
  3. main.py belum menyambung keduanya (tidak ada _auto_recall, tidak ada
     [INGATAN_OTOMATIS]).
  4. core/prompt.txt belum mendefinisikan tag [INGATAN_OTOMATIS].

Yang di-mock: TIDAK ADA. _wants_recall diuji sebagai fungsi NYATA
core.intent; CustomBrain asli dijalankan dengan contoh llm pengganti
(dependensi — bukan fungsi yang diuji), seperti sonde lain memakai contoh
session/ui. Tes perilaku memanggil turn() sungguhan dan membaca pesan yang
benar-benar diterima provider.
"""
import asyncio
import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")
PROMPT_SRC = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
PROMPT_FLAT = " ".join(PROMPT_SRC.split())
CB_SRC = (ROOT / "core" / "custom_brain.py").read_text(encoding="utf-8")

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


# ── 1. detektor deterministik _wants_recall ──────────────────────────────────
try:
    from core.intent import _wants_recall
except Exception as e:                                   # noqa: BLE001
    print(f"  import _wants_recall: {type(e).__name__}: {e} => FAIL")
    errs.append("import _wants_recall")
else:
    _positif = [
        "ingat namaku siapa?",
        "tadi kita bahas apa sih",
        "kemarin aku kirim email ke siapa",
        "password wifi rumahku apa",
        "siapa nama istri aku",
        "sebelumnya kamu bilang folder kerjaku di mana",
        "kita pernah bicara soal proyek itu kan",
        "aku tinggal di mana sih",
        "what was my password again",
        "do you remember my address",
    ]
    for u in _positif:
        check(f"recall+: {u[:44]}", _wants_recall(u) is True)
    _negatif = [
        "kirim email ke rafael subjek laporan",
        "putar musik jazz",
        "buatkan laporan kuartal",
        "siapa presiden indonesia",
        "besok hujan nggak ya",
        "buka youtube",
        "nama tool untuk kirim pesan apa",
        "",
        None,
    ]
    for u in _negatif:
        check(f"bukan-recall: {str(u)[:44]}", _wants_recall(u) is False)

# ── 2. CustomBrain.turn menerima notes ───────────────────────────────────────
try:
    from core.custom_brain import CustomBrain
except Exception as e:                                   # noqa: BLE001
    print(f"  import CustomBrain: {type(e).__name__}: {e} => FAIL")
    errs.append("import CustomBrain")
else:
    _sig = inspect.signature(CustomBrain.turn)
    _p = _sig.parameters.get("notes")
    check("param notes ada di turn()", _p is not None, str(_sig))
    check("param notes punya bawaan", _p is not None and _p.default is None)

    if _p is not None:
        # Perilaku nyata: llm pengganti hanya MENYIMPAN pesan yang diterima.
        class _StubLLM:
            def __init__(self):
                self.seen = None

            def complete(self, messages, tools=None, timeout=120):
                self.seen = [dict(m) for m in messages]
                return "BALESAN-UJI", []

        async def _exec(name, args):
            return "ok"

        brain = CustomBrain(_StubLLM(), "SISTEM-DASAR")
        asyncio.run(brain.turn("ingat namaku siapa", [], _exec,
                               notes="FAKTA-UJI: nama aris"))
        msgs = brain.llm.seen
        check("notes disuntik ke pesan system",
              bool(msgs) and "FAKTA-UJI: nama aris" in str(msgs[0].get("content", "")),
              str(msgs[0])[:80] if msgs else "(tak ada pesan)")
        check("tag ingat otomatis menyertai",
              bool(msgs) and "[INGATAN_OTOMATIS]" in str(msgs[0].get("content", "")))
        check("system dasar tetap utuh",
              bool(msgs) and "SISTEM-DASAR" in str(msgs[0].get("content", "")))
        check("ucapan tetap jadi pesan user",
              any(m.get("role") == "user" and "ingat namaku siapa" in
                  str(m.get("content", "")) for m in msgs))

        brain2 = CustomBrain(_StubLLM(), "SISTEM-DASAR")
        asyncio.run(brain2.turn("halo", [], _exec))
        msgs2 = brain2.llm.seen
        check("tanpa notes -> system tak berubah",
              bool(msgs2) and "FAKTA" not in str(msgs2[0].get("content", ""))
              and "SISTEM-DASAR" in str(msgs2[0].get("content", "")))

        brain3 = CustomBrain(_StubLLM(), "SISTEM-DASAR")
        asyncio.run(brain3.turn("halo", [], _exec, context=[
            {"role": "user", "content": "dari konteks"},
            {"role": "assistant", "content": "balasan konteks"},
        ], notes=""))
        check("notes kosong -> tak menyentuh system",
              "FAKTA" not in str(brain3.llm.seen[0].get("content", "")))

# ── 3. main.py menyambung detektor ke otak ───────────────────────────────────
check("main: _wants_recall dipakai", "_wants_recall" in MAIN_SRC)
check("main: notes diteruskan ke brain", "notes=" in MAIN_SRC)
check("main: pengumpul ingat ada", "def _auto_recall" in MAIN_SRC)
check("main: dijalankan lewat flag recall.auto",
      '"recall.auto"' in MAIN_SRC and "_flag(" in MAIN_SRC)

# Tag tinggal di titik suntik (custom_brain) — satu definisi, satu tempat.
check("otak: tag ingat otomatis disuntik",
      "[INGATAN_OTOMATIS]" in CB_SRC and "RECALL_NOTE_TAG" in CB_SRC)

# ── 4. prompt mendefinisikan tag ─────────────────────────────────────────────
check("prompt: tag ingat otomatis", "[INGATAN_OTOMATIS]" in PROMPT_FLAT)

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
