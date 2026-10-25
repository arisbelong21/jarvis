"""Sonde: kosakata pembagian kerja — Indonesia DAN Inggris.

Permintaan bos (23 Okt): selipkan bahasa Inggris untuk jaga-jaga, dan
perluas daftar kata untuk kedua jalur:
  - jalur BERAT (otak custom): mengedit, edit, membuat, buat, melakukan,
    lakukan, dan kata kerja lain
  - jalur RINGAN (Gemini): buka, refresh, cek, lihat, tampilkan, putar,
    dan sinonimnya — yang penting bukan tindakan yang butuh berpikir
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M                                                # noqa: E402
from core.intent import is_write_intent                          # noqa: E402

ERRORS = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        ERRORS.append(name)


# ── A. klaim tindakan: Indonesia + Inggris ────────────────────────────────
claims = M.claims_action
print("== klaim tindakan ==")
for t in ("Saya buatkan filenya", "aku sudah kirim emailnya",
          "I created the file for you", "I have sent the email",
          "I built the website", "I edited the config"):
    check(f"klaim: {t[:32]!r}", claims(t) is True)
for t in ("Buka YouTube sekarang", "Berikut cara membuat file di Python",
          "Here is how to create a file in Python", "The schedule is empty"):
    check(f"bukan klaim: {t[:32]!r}", claims(t) is False)

# ── B. niat menulis (gerbang deterministik) ───────────────────────────────
print("\n== niat menulis: kata kerja BARU harus tertangkap ==")
BERAT = [
    # Indonesia
    "lakukan sekarang", "kerjakan tugas itu", "rekap penjualan bulan ini",
    "rangkum dokumen ini", "rapikan folder kerja", "pindahkan file ke desktop",
    "salin data ke backup", "unduh videonya", "pasang plugin baru",
    "bersihkan cache", "ekspor ke csv", "terjemahkan halaman ini",
    "ringkas laporan itu", "gabungkan dua file", "cadangkan folder",
    # Inggris
    "edit the file", "build the site", "fix this bug", "refactor the module",
    "deploy the app", "save the file", "add a button", "remove duplicates",
    "make a report", "modify the config", "rewrite this section",
    "summarize this document", "translate this page", "convert to pdf",
    "export to csv", "schedule a meeting",
]
for t in BERAT:
    check(f"berat: {t[:30]!r}", is_write_intent(t) is True)

print("\n== perintah RINGAN harus tetap False ==")
RINGAN = [
    "buka youtube", "cek jadwal saya", "refresh halaman", "lihat berita",
    "putar video tadi", "tampilkan status sistem", "cari berita hari ini",
    "open chrome", "show my calendar", "play some music",
    "what's the weather today", "check my schedule",
]
for t in RINGAN:
    check(f"ringan: {t[:30]!r}", is_write_intent(t) is False)

# ── C. perintah kendali mode tetap utuh ───────────────────────────────────
print("\n== perintah kendali mode ==")
check("'kerjakan di otak custom' -> force",
      M._mode_command("kerjakan di otak custom") == "force",
      str(M._mode_command("kerjakan di otak custom")))
check("'mode default' tetap default",
      M._mode_command("mode default") == "default")
check("'jangan alihkan ke custom' tetap None",
      M._mode_command("jangan alihkan ke custom") is None)

# ── D. pemicu tool ringan (hint) ──────────────────────────────────────────
print("\n== hint tool untuk perintah ringan ==")
hints = M._hinted_tool_names
check("'tampilkan jadwal' melampirkan kalender",
      "calendar_assistant" in hints("tampilkan jadwal saya"),
      str(hints("tampilkan jadwal saya")))
check("'refresh halaman' melampirkan browser",
      "browser_control" in hints("refresh halaman ini"),
      str(hints("refresh halaman ini")))
check("'putar musik' melampirkan pemutar video",
      "video_player" in hints("putar musik di youtube"),
      str(hints("putar musik di youtube")))

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
