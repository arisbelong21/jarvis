"""Sonde: ringkasan sesi (curhat) harus BISA DIBACA, bukan hanya disimpan.

Dijalankan SEBELUM perbaikan -> harus MERAH.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memory import memory_manager as mm

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


# Memori sementara — TIDAK menyentuh long_term.json milik pengguna.
tmp = Path(tempfile.mkdtemp()) / "long_term.json"
tmp.write_text(json.dumps({
    "identity": {"name": {"value": "Aris"}},
    "notes": {},
    "sessions": [
        {"date": "2026-10-20", "summary": "Pengguna membahas pekerjaan kantor."},
        {"date": "2026-10-24",
         "summary": "Pengguna curhat soal mantan bernama Kumaran yang selingkuh."},
    ],
}, ensure_ascii=False), encoding="utf-8")
_orig = mm.MEMORY_PATH
mm.MEMORY_PATH = tmp

print("== 1. ringkasan sesi bisa dicari (recall_memory) ==")
found = mm.search_memory("kumaran")
check("query 'kumaran' menemukan ringkasan sesi", "Kumaran" in found, True)
check("hasilnya diberi label sessions", "session" in found.lower(), True)

print("\n== 2. ringkasan sesi masuk blok prompt (kontinuitas) ==")
block = mm.format_memory_for_prompt(mm.load_memory())
check("blok prompt memuat 'Percakapan terakhir'",
      "Percakapan terakhir" in block, True)
check("blok prompt memuat isi ringkasan terbaru",
      "Kumaran" in block, True)
check("ringkasan tidak mendominasi prompt (<= 300 char)",
      len(block) < 3000, True)

print("\n== 3. kapasitas sesi cukup untuk diingat ==")
check("_SESSION_MAX >= 10", mm._SESSION_MAX >= 10, True)

print("\n== 4. potongan ringkasan tidak memotong kata ==")
clip = mm._clip_summary("kata " * 200, 120)
check("panjang <= batas", len(clip) <= 120, True)
check("tidak berakhir di tengah kata",
      clip.rstrip("…").endswith("kata"), True)
check("diberi penanda terpotong", clip.endswith("…"), True)
check("teks pendek tidak diubah", mm._clip_summary("halo dunia", 120),
      "halo dunia")

print("\n== 5. briefing tidak menghapus ringkasan ==")
first = mm.pop_last_session()
check("pop mengembalikan entri terbaru", "Kumaran" in (first or {}).get(
    "summary", ""), True)
second = mm.pop_last_session()
check("pop kedua mengembalikan entri berikutnya", "kantor" in (second or {}).get(
    "summary", ""), True)
third = mm.pop_last_session()
check("setelah semua dibriefing -> None", third, None)
kept = mm.load_memory().get("sessions") or []
check("ringkasan TETAP tersimpan setelah briefing", len(kept), 2)

mm.MEMORY_PATH = _orig

print("\n== 6. cadangan saat ringkasan model gagal (kuota habis) ==")
import main as M

_fb = M._fallback_summary([
    "User: halo jarvis",
    "JARVIS: halo juga",
    "User: saya cerita soal mantan saya yang selingkuh",
    "JARVIS: maaf ya",
])
check("cadangan memuat ucapan pengguna", "selingkuh" in _fb, True)
check("tanpa ucapan pengguna -> kosong",
      M._fallback_summary(["JARVIS: hanya asisten"]), "")
_src_main = (ROOT / "main.py").read_text(encoding="utf-8")
check("cadangan dipakai saat ringkasan kosong/gagal",
      _src_main.count("_fallback_summary(log)") >= 2, True)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
