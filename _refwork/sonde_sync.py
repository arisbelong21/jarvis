"""Sonde: badge model jujur, koneksi pulih sendiri, hasil pekerjaan tercatat.

Tiga kejadian nyata (24 Okt, log bos):
  1. UI tetap menampilkan "claude" padahal GLM yang menjawab. Penyebab: saat
     connect, badge ditulis `live_model` (model Gemini) — bukan otak yang
     benar-benar menjawab; dan setiap reconnect menimpanya lagi.
  2. JARVIS STUCK setelah "Koneksi Live terputus saat kirim audio". Jalur
     kirim hanya menandai `_live_down` lalu membuang frame — tidak pernah
     meminta sambung ulang. Kalau jalur terima juga diam, tidak ada yang
     memulihkan.
  3. Dua otak tidak sinkron: file HTML sudah dibuat, tetapi sesudah restart
     otak menjawab "belum ada website, saya baru mencari". Hasil pekerjaan
     tidak pernah dicatat ke riwayat yang dibaca otak.
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


# ── A. label badge ────────────────────────────────────────────────────────
label = getattr(M, "_badge_label", None)
check("_badge_label ada", callable(label), "tidak ada")
if callable(label):
    h = [{"pid": "flagship", "model": "claude-opus-5.5", "resting_s": 300.0},
         {"pid": "river", "model": "zai-org/GLM-5.3-Flash", "resting_s": 0.0}]
    check("hybrid + terakhir river -> tampil GLM (river)",
          "GLM" in label(True, "flagship", "claude-opus-5.5", "river", h),
          label(True, "flagship", "claude-opus-5.5", "river", h))
    check("hybrid tanpa riwayat -> tampil model terpilih",
          "claude" in label(True, "flagship", "claude-opus-5.5", "", h),
          label(True, "flagship", "claude-opus-5.5", "", h))
    check("mode live -> tampil model Gemini",
          "gemini" in label(False, "flagship", "claude-opus-5.5", "", h).lower(),
          label(False, "flagship", "claude-opus-5.5", "", h))

# ── B. pemulihan koneksi ──────────────────────────────────────────────────
ask = getattr(M, "_should_ask_reconnect", None)
check("_should_ask_reconnect ada", callable(ask), "tidak ada")
if callable(ask):
    check("koneksi mati & belum diminta -> minta",
          ask(live_down=True, already=False, now=100.0, last=0.0) is True)
    check("sudah diminta < 5 detik lalu -> jangan ulang",
          ask(live_down=True, already=True, now=100.0, last=98.0) is False)
    check("koneksi hidup -> tidak perlu",
          ask(live_down=False, already=False, now=100.0, last=0.0) is False)

SRC = (ROOT / "main.py").read_text(encoding="utf-8")
check("jalur kirim audio meminta sambung ulang",
      "Koneksi Live terputus saat kirim audio" in SRC
      and "_should_ask_reconnect(" in SRC)

# ── C. hasil pekerjaan tercatat ───────────────────────────────────────────
check("hasil pekerjaan dicatat ke riwayat otak",
      "_job_record(" in SRC)
rec = getattr(M, "_job_record", None)
check("_job_record ada", callable(rec), "tidak ada")
if callable(rec):
    line = rec("code_helper: buat web", "Code written. Saved to: C:/x/y.html")
    check("catatan memuat judul pekerjaan", "code_helper" in line, line[:60])
    check("catatan memuat hasil", "Saved to" in line, line[:60])
    check("catatan bertanda agar terbaca otak",
          "PEKERJAAN" in line.upper(), line[:60])

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
