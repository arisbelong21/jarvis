"""Sonde: perintah mode harus lihat KONTEKS, dan masuk mode curhat wajib
membaca ringkasan curhat sebelumnya.

Dijalankan SEBELUM perbaikan -> harus MERAH.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M
from core.decide import decide_turn

ERRORS: list[str] = []
OK = 0


def check(name: str, got, want) -> None:
    global OK
    if got == want:
        OK += 1
        print(f"  OK   {name}")
    else:
        ERRORS.append(name)
        print(f"  FAIL {name}: dapat {got!r}, harusnya {want!r}")


print("== 1. membahas mode != meminta mode (kejadian nyata di log) ==")
check("pertanyaan soal mode curhat TIDAK mengaktifkan",
      M._vent_command("berarti tadi selama mode curhat kamu nggak bisa "
                      "nyimpen di memori ya?"), None)
check("pertanyaan 'sudah keluar?' TIDAK mematikan",
      M._vent_command("apakah sudah keluar dari mode curhat?"), None)
check("melaporkan 'tadi saya minta masuk mode curhat' TIDAK mengaktifkan",
      M._vent_command("tadi saya minta kamu masuk mode curhat"), None)
check("'kamu bilang mode curhat itu apa?' TIDAK mengaktifkan",
      M._vent_command("kamu bilang mode curhat itu apa?"), None)

print("\n== 2. perintah yang sah tetap jalan ==")
check("'aktifkan mode curhat' -> on",
      M._vent_command("aktifkan mode curhat"), "on")
check("'masuk mode curhat' -> on",
      M._vent_command("masuk mode curhat"), "on")
check("'mode curhat' pendek -> on", M._vent_command("mode curhat"), "on")
check("'jarvis tolong masuk mode curhat' -> on",
      M._vent_command("jarvis tolong masuk mode curhat"), "on")
check("'matikan mode curhat' -> off",
      M._vent_command("matikan mode curhat"), "off")
check("'matikan mode curhat ya?' -> off",
      M._vent_command("matikan mode curhat ya?"), "off")
check("negasi tetap ditolak",
      M._vent_command("jangan aktifkan mode curhat"), None)

print("\n== 3. perintah ganti model juga lihat konteks ==")
check("pertanyaan soal model tidak mengganti",
      M._vent_command("apakah tadi kamu ganti model ke claude?"), None)
check("perintah ganti model tetap jalan",
      bool(M._switch_command("ganti otak ke glm 5.3")), True)
check("pertanyaan 'model apa yang dipakai?' tidak mengganti",
      M._switch_command("model apa yang dipakai?"), None)

print("\n== 4. konteks curhat disuntik saat mode aktif ==")
_sessions = [
    {"date": "2026-10-20", "summary": "Pengguna bercerita soal pekerjaan."},
    {"date": "2026-10-24", "summary": "Pengguna curhat soal mantan Kumaran."},
]
ctx = M._vent_context_text(_sessions)
check("konteks memuat ringkasan terbaru", "Kumaran" in ctx, True)
check("konteks diberi penanda jangan dibacakan",
      "JANGAN DIBACAKAN" in ctx, True)
check("konteks memuat tanggal", "2026-10-24" in ctx, True)
check("tanpa sesi -> string kosong", M._vent_context_text([]), "")

_src = (ROOT / "main.py").read_text(encoding="utf-8")
check("hook mode curhat menyuntik konteks",
      "_inject_vent_context" in _src, True)

print("\n== 5. tool memori tetap boleh di mode curhat ==")
for nm in ("save_memory", "recall_memory"):
    check(f"{nm} di mode curhat -> pass",
          decide_turn("simpan", name=nm, vent_mode=True)["action"], "pass")
check("brain tetap dimatikan di mode curhat",
      decide_turn("buat laporan", name="brain", vent_mode=True)["action"],
      "small")

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
