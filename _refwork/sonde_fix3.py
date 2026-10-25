"""Sonde: tiga perbaikan dari log 24 Okt (hanya yang bermasalah).

1. "switch model ke Cloud Opus 5.5" dijawab sendiri oleh Gemini ("tidak bisa")
   padahal tool `switch_model` ADA. Gerbang hanya berjalan kalau tool dipanggil,
   jadi perintah kendali harus ditangkap dari TRANSKRIP.
2. Email terkirim DUA KALI (job2 & job4) karena otak mengirim ulang.
3. Zona waktu disalahkan ke Google Calendar tanpa verifikasi.
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


# ── 1. perintah kendali model ─────────────────────────────────────────────
sw = getattr(M, "_switch_command", None)
check("_switch_command ada", callable(sw), "tidak ada")
if callable(sw):
    got = sw("Jervis switch model ke Cloud Opus 5.5 dari flagship")
    check("memahami 'switch model ke X dari Y'",
          isinstance(got, dict) and "Opus" in got.get("model", "")
          and got.get("provider") == "flagship", str(got))
    got = sw("tolong ganti otak ke glm 5.3")
    check("memahami 'ganti otak ke X'",
          isinstance(got, dict) and "glm" in got.get("model", "").lower(), str(got))
    for t in ("buka youtube", "ganti wallpaper", "jangan ganti model",
              "model apa yang dipakai?", "cek jadwal saya"):
        check(f"bukan perintah: {t[:26]!r}", sw(t) is None, str(sw(t)))

# ── 2. anti-duplikasi pekerjaan ───────────────────────────────────────────
dup = getattr(M, "_is_duplicate_job", None)
check("_is_duplicate_job ada", callable(dup), "tidak ada")
if callable(dup):
    sig = 'composio_actions|{"to":"a@b.c","subject":"x"}'
    check("job identik dalam 60 detik -> duplikat",
          dup(sig, sig, 10.0) is True)
    check("job identik setelah 60 detik -> bukan duplikat",
          dup(sig, sig, 120.0) is False)
    check("job berbeda -> bukan duplikat",
          dup(sig, 'composio_actions|{"to":"z@b.c"}', 5.0) is False)
    check("belum ada job sebelumnya -> bukan duplikat",
          dup("", sig, 5.0) is False)

# ── 3. zona waktu: jangan menyalahkan kalender ────────────────────────────
SRC = (ROOT / "main.py").read_text(encoding="utf-8")
check("aturan zona waktu ada di prompt otak",
      "zona waktu" in SRC.lower() and "jangan menyalahkan" in SRC.lower())

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
