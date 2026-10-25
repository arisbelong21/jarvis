"""Sonde: mulut tidak boleh diam saat otak sudah punya jawaban.

Gejala 23 Okt: activity log hanya "You: ..." — otak menjawab, tetapi tidak
ada baris JARVIS dan tidak ada suara.

Bukti penyelidikan (probe 1-8):
  - model & prompt bukan penyebab (probe 1-3: selalu bersuara)
  - jalur terima aplikasi bukan penyebab (probe 3 & 7: bersuara, loop 20 ms)
  - VAD start=high + derau ruangan -> DIAM 1/5 (probe 8); start=low -> 0/5
    Jadi mulut BISA dibatalkan server secara intermiten.

Karena pemicunya intermiten, aplikasi tidak boleh bergantung pada "model
pasti bersuara". Dua penjaga yang diuji di sini:

  A. `_mic_stream_allowed` — mic tidak dialirkan selama otak bekerja dan
     selama menunggu suara pertama. Derau yang mengalir di jendela itu
     adalah pemicu VAD membatalkan giliran.
  B. `_mouth_fallback_due` — kalau tidak ada audio/transkrip dalam masa
     tenggang, jawaban otak diucapkan lewat jalur yang sudah terbukti
     (send_client_content), bukan dibiarkan hilang.
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


# ── A. gerbang mic ────────────────────────────────────────────────────────
gate = getattr(M, "_mic_stream_allowed", None)
check("helper _mic_stream_allowed tersedia", callable(gate),
      "tidak ada" if gate is None else "")

if callable(gate):
    check("mic TERTUTUP saat otak bekerja",
          gate(muted=False, phone_active=False, in_brain=True,
               awaiting_reply=False) is False)
    check("mic TERTUTUP saat menunggu suara pertama",
          gate(muted=False, phone_active=False, in_brain=False,
               awaiting_reply=True) is False)
    check("mic TERBUKA saat menganggur (perilaku lama)",
          gate(muted=False, phone_active=False, in_brain=False,
               awaiting_reply=False) is True)
    check("mic TERTUTUP saat muted (perilaku lama)",
          gate(muted=True, phone_active=False, in_brain=False,
               awaiting_reply=False) is False)
    check("mic TERTUTUP saat telepon aktif (perilaku lama)",
          gate(muted=False, phone_active=True, in_brain=False,
               awaiting_reply=False) is False)

# ── B. jatuh-tempo mulut ──────────────────────────────────────────────────
due = getattr(M, "_mouth_fallback_due", None)
check("helper _mouth_fallback_due tersedia", callable(due),
      "tidak ada" if due is None else "")

if callable(due):
    t0 = 1000.0
    check("belum jatuh tempo bila belum ada jawaban tertunda",
          due(None, t0 + 99, got_audio=False) is False)
    check("belum jatuh tempo di 3 detik",
          due(t0, t0 + 3.0, got_audio=False) is False)
    check("jatuh tempo setelah masa tenggang",
          due(t0, t0 + 7.0, got_audio=False) is True)
    check("TIDAK jatuh tempo bila audio sudah datang",
          due(t0, t0 + 7.0, got_audio=True) is False)
    check("masa tenggang bisa diatur",
          due(t0, t0 + 7.0, got_audio=False, grace=30.0) is False)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
