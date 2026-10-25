"""Sonde: sesi Live diputar SEBELUM batas server, dan hanya saat menganggur.

Bukti yang sudah dikumpulkan:
  - probe 10: tanpa audio 100 s -> sesi tetap hidup (bukan idle)
  - probe 11: loop penerima diblokir 70 s -> sesi tetap hidup (bukan blokir)
  - log bos: `1008 ... GoAway signal once the session durat...` lalu
    "Session reached its time limit — reconnecting."
    -> server punya BATAS DURASI sesi; itu yang memutus tugas panjang.

Yang diuji:
  A. `_should_rotate_session` — putar sesi hanya bila sudah tua DAN menganggur.
  B. batas lunak harus di bawah batas server (600 s) supaya sempat mendahului.
  C. `_job_step_should_log` — alur kerja latar masuk activity log tanpa membanjiri.
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


# ── A. putar sesi ─────────────────────────────────────────────────────────
rot = getattr(M, "_should_rotate_session", None)
check("helper _should_rotate_session tersedia", callable(rot),
      "tidak ada" if rot is None else "")

if callable(rot):
    lim = 480.0
    check("sesi masih muda -> tidak diputar",
          rot(age=100.0, busy=False, limit=lim) is False)
    check("sesi tua + menganggur -> diputar",
          rot(age=500.0, busy=False, limit=lim) is True)
    check("sesi tua tapi SEDANG BEKERJA -> jangan diputar",
          rot(age=500.0, busy=True, limit=lim) is False)
    check("tepat di batas -> diputar",
          rot(age=lim, busy=False, limit=lim) is True)

# ── B. batas lunak di bawah batas server ──────────────────────────────────
check("batas lunak ada dan di bawah 600 s",
      hasattr(M, "_SESSION_SOFT_LIMIT_S") and M._SESSION_SOFT_LIMIT_S < 600,
      f"{getattr(M, '_SESSION_SOFT_LIMIT_S', 'tidak ada')}")

# ── C. log langkah kerja (tidak membanjiri) ───────────────────────────────
logstep = getattr(M, "_job_step_should_log", None)
check("helper _job_step_should_log tersedia", callable(logstep),
      "tidak ada" if logstep is None else "")

if callable(logstep):
    check("langkah pertama -> dicatat",
          logstep("Writing index.html", "", 0.0, now=100.0) is True)
    check("langkah sama dalam 2 detik -> tidak dicatat",
          logstep("Writing index.html", "Writing index.html",
                  99.0, now=100.0) is False)
    check("langkah sama setelah 2 detik -> dicatat lagi",
          logstep("Writing index.html", "Writing index.html",
                  90.0, now=100.0) is True)
    check("langkah berbeda -> dicatat",
          logstep("Writing admin.html", "Writing index.html",
                  99.5, now=100.0) is True)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
