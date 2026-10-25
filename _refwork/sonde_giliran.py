# -*- coding: utf-8 -*-
"""Probe Fase 4 Slice 1 — klasifikasi giliran (11 tipe, enforcement kode).

MERAH sebelum implementasi (harus gagal terhadap kode SEKARANG):
  1. core.intent belum punya classify_turn (penanda 11 tipe giliran).
  2. main.py belum menghubungkannya: tidak ada klasifikasi di _brain_turn,
     tidak ada [TIDAK_ADA_JAWABAN], tidak ada gerbang flag.
  3. config belum punya sakelar voice.turn_classify.
  4. core/prompt.txt belum mendefinisikan [TIDAK_ADA_JAWABAN].
  5. Intent router lama (WRITE/READ/gemini/claims_done) harus TETAP utuh —
     Slice 1 menambah, tidak mengubah router yang sudah jalan.

Yang di-mock: TIDAK ADA. classify_turn diuji sebagai fungsi NYATA
core.intent; config dibaca lewat config.get_flags() sungguhan; main.py
dan prompt dibaca sebagai berkas (gaya sonde rumah).
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


def _func_src(src: str, header: str) -> str:
    """Potongan sumber satu method: dari header sampai method berikutnya."""
    i = src.find(header)
    if i < 0:
        return ""
    j = len(src)
    for nxt in ("\n    async def ", "\n    def "):
        k = src.find(nxt, i + len(header))
        if 0 < k < j:
            j = k
    return src[i:j]


# ── 1. classify_turn: 11 tipe, deterministik ─────────────────────────────────
try:
    from core.intent import classify_turn, TURN_TYPES
except Exception as e:                                   # noqa: BLE001
    print(f"  import classify_turn: {type(e).__name__}: {e} => FAIL")
    errs.append("import classify_turn")
else:
    check("11 tipe terdaftar", len(TURN_TYPES) == 11, f"{len(TURN_TYPES)} tipe")
    _harus = {"work_request", "not_for_me", "dictation", "recall", "refusal",
              "control", "farewell", "confirmation", "small_talk", "idle",
              "unclear"}
    check("nama tipe sesuai rencana", set(TURN_TYPES) == _harus,
          ",".join(sorted(set(TURN_TYPES) ^ _harus)) or "cocok")

    # Positif — satu kalimat per tipe, sesuai spec sonde di rencana.
    _kasus = [
        ("kirim email ke raka subjek laporan", "work_request"),
        ("buatkan laporan kuartal", "work_request"),
        ("tolong reply pesan tadi", "work_request"),
        ("jadwalkan rapat besok", "work_request"),
        ("kirah-kira 3 meter ya", "not_for_me"),
        ("dek, ambilkan air mineral", "not_for_me"),
        ("ngomong ke pak bambu dulu", "not_for_me"),
        ("bukan ke kamu, ini ke dia", "not_for_me"),
        ("catat ini: rapat besok jam sembilan", "dictation"),
        ("dengarkan baik-baik ya, aku bacakan nomornya", "dictation"),
        ("dikte nomor rekening", "dictation"),
        ("tadi kita bahas apa", "recall"),
        ("ingat namaku siapa", "recall"),
        ("kemarin aku kirim email ke siapa", "recall"),
        ("batal", "refusal"),
        ("gak jadi", "refusal"),
        ("jangan dulu", "refusal"),
        ("batalin itu", "refusal"),
        ("diam", "control"),
        ("ulangi", "control"),
        ("pelan-pelan", "control"),
        ("lebih keras sedikit", "control"),
        ("sudah, makasih", "farewell"),
        ("sampai jumpa", "farewell"),
        ("dah, cukup sekian", "farewell"),
        ("terima kasih", "farewell"),
        ("lanjut", "confirmation"),
        ("lanjutkan", "confirmation"),
        ("gas", "confirmation"),
        ("setuju", "confirmation"),
        ("silakan", "confirmation"),
        ("hai", "small_talk"),
        ("halo", "small_talk"),
        ("apa kabar", "small_talk"),
        ("selamat pagi", "small_talk"),
        ("ceritakan lelucon", "small_talk"),
        ("hmm", "idle"),
        ("oh gitu", "idle"),
        ("begitu ya", "idle"),
        ("oalah", "idle"),
        ("oke", "unclear"),
        ("ok", "unclear"),
        ("oh ya", "unclear"),
        ("apa ya", "unclear"),
    ]
    for _u, _exp in _kasus:
        check(f"giliran {_u[:40]!r} -> {_exp}",
              classify_turn(_u) == _exp, f"dapat {classify_turn(_u)}")

    # Sempelan regresi — yang BUKAN tipenya (false-positive mahal).
    check("'oke' BUKAN farewell", classify_turn("oke") != "farewell")
    check("'oke' BUKAN small_talk", classify_turn("oke") != "small_talk")
    check("kerjaan BUKAN small_talk",
          classify_turn("kirim email ke raka") not in ("small_talk", "unclear"))
    check("tanya masa lalu BUKAN work_request",
          classify_turn("tadi kita bahas apa") != "work_request")
    check("pamit BUKAN unclear", classify_turn("sudah, makasih") != "unclear")
    check("bicara-ke-orang-lain BUKAN small_talk",
          classify_turn("kirah-kira 3 meter ya") != "small_talk")
    check("'lanjut' = confirmation (bukan unclear)",
          classify_turn("lanjut") not in ("unclear", "farewell"))

    # Deterministik & defensif.
    check("deterministik (3x sama)",
          len({classify_turn("kirah-kira 3 meter ya") for _ in range(3)}) == 1)
    for _kosong in ("", None, "   "):
        _k = classify_turn(_kosong)
        check(f"masukan {str(_kosong)!r} tak meledak",
              isinstance(_k, str) and _k in TURN_TYPES, f"dapat {_k}")

    # Router lama HARUS tetap utuh — Slice 1 menambah, tidak mengubah.
    from core.intent import (is_write_intent, is_read_only, allows_gemini,
                             claims_done, _wants_recall)
    check("is_write_intent tetap", is_write_intent("kirim email ke raka")
          and not is_write_intent("buka youtube"))
    check("is_read_only tetap", is_read_only("buka youtube")
          and not is_read_only("kirim email ke raka"))
    check("allows_gemini tetap", allows_gemini("kamu saja yang kerjakan"))
    check("claims_done tetap", claims_done("sudah saya kirim"))
    check("_wants_recall tetap", _wants_recall("ingat namaku siapa")
          and not _wants_recall("kirim email ke raka"))

# ── 2. main.py: klasifikasi SEBELUM daftar tool disusun ──────────────────────
_imp = ""
_i = MAIN_SRC.find("from core.intent import")
if _i >= 0:
    _imp = MAIN_SRC[_i:MAIN_SRC.find(")", _i) + 1]
check("main: classify_turn diimpor", "classify_turn" in _imp)

_seg = _func_src(MAIN_SRC, "async def _brain_turn")
check("main: _brain_turn ada", bool(_seg))
check("main: classify_turn dipanggil di _brain_turn", "classify_turn(" in _seg)
check("main: klasifikasi SEBELUM tools dibangun",
      _seg.find("classify_turn") >= 0 and _seg.find("tools = "
      "to_openai") > _seg.find("classify_turn") >= 0)
check("main: gerbang flag voice.turn_classify",
      '"voice.turn_classify"' in _seg and "_flag(" in _seg)
check("main: not_for_me -> tag diam", "_NOT_FOR_ME_TAG" in _seg)
check("main: small_talk/farewell/dictation tanpa tool",
      '("small_talk", "farewell", "dictation")' in _seg)
check("main: dikte tidak memicu recall",
      '_kind != "dictation"' in _seg)
check("main: tag diam cuma satu titik",
      MAIN_SRC.count('"[TIDAK_ADA_JAWABAN]"') >= 1)
check("main: pemanggil menerjemahkan tag (>=3 titik)",
      MAIN_SRC.count("== _NOT_FOR_ME_TAG") >= 3,
      f"{MAIN_SRC.count('== _NOT_FOR_ME_TAG')} titik")
check("main: konstanta tag didefinisikan", "_NOT_FOR_ME_TAG = " in MAIN_SRC)

# ── 3. config: sakelar rollback ──────────────────────────────────────────────
try:
    import config
    _flags = config.get_flags()
    check("config: voice.turn_classify ada di default",
          "voice.turn_classify" in config.VOICE_FLAG_DEFAULTS)
    check("config: voice.turn_classify default ON",
          config.VOICE_FLAG_DEFAULTS.get("voice.turn_classify") is True)
    check("config: get_flags membacanya", _flags.get("voice.turn_classify") is True)
except Exception as e:                                   # noqa: BLE001
    check("config: get_flags", False, f"{type(e).__name__}: {e}")

# ── 4. prompt: tag diam didefinisikan ────────────────────────────────────────
check("prompt: tag [TIDAK_ADA_JAWABAN]", "[TIDAK_ADA_JAWABAN]" in PROMPT_FLAT)
check("prompt: instruksi akhiri giliran tanpa bicara",
      "Akhiri giliran tanpa bicara" in PROMPT_FLAT)

# ── 2b. audit 1: giliran tanpa tool harus benar-benar NOL tool ────────────────
# Bug audit 1: turn() selalu menambahkan _extra (tool hasil search_tools di
# giliran lalu) ke daftar yang dikirim — janji "sapaan/pamit/dikte tanpa
# tool" bocor. clear_extra() menutupnya; ini menguji PERILAKUNYA, bukan nama.
try:
    from core.custom_brain import CustomBrain

    class _StubLLM2:
        def __init__(self):
            self.tools_seen = None

        def complete(self, messages, tools=None, timeout=120):
            self.tools_seen = list(tools or [])
            return "OK", []

    async def _exec2(name, args):
        return "ok"

    _b = CustomBrain(_StubLLM2(), "SISTEM-UJI")
    _b.register_tools([{"name": "mcp_decompile",
                        "description": "contoh tool on-demand"}])
    check("otak: tool on-demand terdaftar",
          "mcp_decompile" in _b._extra)
    _b.clear_extra()
    check("otak: clear_extra membersihkan", _b._extra == {})
    asyncio.run(_b.turn("hai", [], _exec2))
    check("perilaku: giliran tanpa tool -> provider terima 0 tool",
          _b.llm.tools_seen == [], f"{len(_b.llm.tools_seen)} tool")

    _b2 = CustomBrain(_StubLLM2(), "SISTEM-UJI")
    _b2.register_tools([{"name": "mcp_decompile",
                        "description": "contoh tool on-demand"}])
    _b2.clear_extra()
    _b2.register_tools([{"name": "mcp_strings",
                         "description": "contoh tool on-demand"}])
    check("perilaku: registrasi setelah clear tetap jalan",
          list(_b2._extra) == ["mcp_strings"])
    asyncio.run(_b2.turn("kirim email ke raka", [], _exec2))
    check("perilaku: registrasi baru tetap sampai ke provider",
          any(t.get("name") == "mcp_strings"
              or (t.get("function", {}) or {}).get("name") == "mcp_strings"
              for t in _b2.llm.tools_seen),
          f"{[t.get('name') for t in _b2.llm.tools_seen]}")
except Exception as e:                                   # noqa: BLE001
    check("otak: clear_extra", False, f"{type(e).__name__}: {e}")

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
