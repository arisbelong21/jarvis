"""Sonde: pembagian kerja Gemini vs otak custom — dua bagian yang belum.

1. CELAH: Gemini menjawab tugas BERAT tanpa memanggil tool apa pun, lalu
   mengaku sudah mengerjakan. Guard lama hanya menangkap kata "sudah/telah",
   jadi jawaban seperti "Saya buatkan filenya seperti ini: ..." lolos.
2. SAKELAR SUARA: bos harus bisa memaksa semua pekerjaan ke otak custom
   ("alihkan pekerjaan ke custom otak api") dan mengembalikannya
   ("mode default").

Yang diuji:
  A. `claims_action()` — mendeteksi klaim tindakan orang pertama.
  B. `_mode_command()` — frasa kendali, termasuk penolakan negasi.
  C. `_should_force_brain()` — gerbang pengalihan saat mode paksa.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M                                                # noqa: E402

ERRORS = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        ERRORS.append(name)


# ── A. klaim tindakan ─────────────────────────────────────────────────────
claims = getattr(M, "claims_action", None)
check("claims_action ada", callable(claims), "tidak ada")
if callable(claims):
    for t in ("Saya buatkan filenya sekarang",
              "sudah saya edit bagian header",
              "aku sudah kirim emailnya",
              "Saya rapikan kodenya tadi"):
        check(f"klaim terdeteksi: {t[:30]!r}", claims(t) is True)
    for t in ("Buka YouTube sekarang ya",
              "Berikut cara membuat file di Python",
              "Jadwal kamu hari ini kosong",
              "Mau saya bantu apa?"):
        check(f"bukan klaim: {t[:30]!r}", claims(t) is False)


# ── B. perintah kendali mode ──────────────────────────────────────────────
mode = getattr(M, "_mode_command", None)
check("_mode_command ada", callable(mode), "tidak ada")
if callable(mode):
    for t in ("tolong alihkan pekerjaan ke custom otak api",
              "alihkan semua tugas ke otak custom",
              "pakai otak custom untuk semuanya",
              "serahkan semuanya ke custom api"):
        check(f"-> force: {t[:34]!r}", mode(t) == "force", str(mode(t)))
    for t in ("mode default",
              "kembali ke mode normal",
              "pakai mode otomatis lagi",
              "balik ke mode biasa"):
        check(f"-> default: {t[:34]!r}", mode(t) == "default", str(mode(t)))
    for t in ("jangan alihkan ke custom",
              "buka youtube",
              "cek jadwal saya",
              "model apa yang dipakai"):
        check(f"-> None: {t[:34]!r}", mode(t) is None, str(mode(t)))


# ── C. gerbang pengalihan ─────────────────────────────────────────────────
force = getattr(M, "_should_force_brain", None)
check("_should_force_brain ada", callable(force), "tidak ada")
if callable(force):
    check("mode paksa + tool ringan -> dialihkan",
          force(True, "youtube_video", is_builtin=False) is True)
    check("mode paksa + tool builtin -> TIDAK dialihkan",
          force(True, "recall_memory", is_builtin=True) is False)
    check("mode paksa + brain -> TIDAK dialihkan (sudah di otak)",
          force(True, "brain", is_builtin=True) is False)
    check("mode normal -> tidak pernah memaksa",
          force(False, "youtube_video", is_builtin=False) is False)

# ── D. sambungan nyata di main.py ─────────────────────────────────────────
SRC = (ROOT / "main.py").read_text(encoding="utf-8")
check("flag _force_brain diinisialisasi", "self._force_brain    = False" in SRC
      or "self._force_brain = False" in SRC)
check("perintah 'force' menyalakan flag",
      'if _cmd == "force":' in SRC and "self._force_brain = True" in SRC)
check("perintah 'default' mematikan flag",
      'if _cmd == "default":' in SRC and "self._force_brain = False" in SRC)
check("router memakai _should_force_brain",
      "_should_force_brain(getattr(self, \"_force_brain\", False)" in SRC)
check("guard jawaban berat memakai claims_action",
      "or claims_action(full_out)" in SRC)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
