"""Sonde: perintah mode diputuskan dengan KONTEKS, bukan hanya bentuk kalimat.

Lapis: (1) bentuk kalimat, (2) konteks kalimat, (3) model murah hanya di zona
ambigu, (4) gagal -> jangan lakukan apa pun.

Dijalankan SEBELUM perbaikan -> harus MERAH.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M

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


print("== 1. perintah yang susunannya bebas tetap dikenali ==")
check("'saya mau kamu masuk mode curhat sekarang' -> on",
      M._vent_command("saya mau kamu masuk mode curhat sekarang"), "on")
check("'aku pengen kamu aktifkan mode curhat' -> on",
      M._vent_command("aku pengen kamu aktifkan mode curhat"), "on")
check("'saya mau keluar dari mode curhat' -> off",
      M._vent_command("saya mau keluar dari mode curhat"), "off")
check("'tolong matikan mode curhat' -> off",
      M._vent_command("tolong matikan mode curhat"), "off")

print("\n== 2. kalimat yang MEMBAHAS tetap tidak memicu ==")
check("pertanyaan soal mode curhat",
      M._vent_command("berarti tadi selama mode curhat kamu nggak bisa "
                      "nyimpen ya?"), None)
check("laporan masa lalu",
      M._vent_command("tadi saya suruh kamu masuk mode curhat tapi nggak jalan"),
      None)
check("rencana masa depan",
      M._vent_command("nanti kita masuk mode curhat ya"), None)
check("pertanyaan 'sudah keluar?'",
      M._vent_command("apakah sudah keluar dari mode curhat?"), None)

print("\n== 3. zona ambigu dikenali (bukan ditebak) ==")
check("bahasan tanpa tanda tanya -> ambigu",
      M._vent_ambiguous("mode curhat itu penting ya buat kamu"), True)
check("perintah jelas bukan ambigu",
      M._vent_ambiguous("masuk mode curhat"), False)
check("pertanyaan jelas bukan ambigu",
      M._vent_ambiguous("apakah mode curhat itu bagus?"), False)
check("tanpa kata mode -> bukan ambigu",
      M._vent_ambiguous("halo jarvis"), False)

print("\n== 4. jawaban model dibaca ketat ==")
check("'PERINTAH' -> perintah", M._parse_verdict("PERINTAH"), "perintah")
check("'bahas' -> bahas", M._parse_verdict("bahas"), "bahas")
check("jawaban ngawur -> None", M._parse_verdict("mungkin iya mungkin tidak"),
      None)
check("jawaban kosong -> None", M._parse_verdict(""), None)

print("\n== 5. sambungan nyata di main.py ==")
_src = (ROOT / "main.py").read_text(encoding="utf-8")
check("ada classifier model", "_classify_vent_command" in _src, True)
check("ada penerap status terpusat", "_apply_vent_state" in _src, True)
check("classifier memakai tier murah", "gemini.FAST" in _src, True)
check("classifier menyertakan konteks percakapan",
      "Konteks percakapan terakhir" in _src, True)
check("hook memakai jalur ambigu",
      "_vent_ambiguous" in _src.split("def _vent_ambiguous")[0].split(
          "_receive_audio")[-1] or "_vent_ambiguous(" in _src, True)
# 25 Okt — konteks harus SELESAI terkirim sebelum konfirmasi diucapkan.
check("konteks ditunggu, bukan ditinggal (create_task)",
      "await asyncio.wait_for(self._inject_vent_context()" in _src, True)
check("pemanggil menunggu penerap status",
      "await self._apply_vent_state(" in _src, True)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
