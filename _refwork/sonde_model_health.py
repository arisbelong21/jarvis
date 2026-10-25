"""Sonde: model_status harus melaporkan KESEHATAN provider, bukan cuma config.

Kejadian 24 Okt (log bos): flagship kena HTTP 429 (free usage limit), river
yang menjawab — tetapi `model_status` melaporkan "Otak percakapan:
claude-opus-5.5 lewat flagship", jadi otak menyimpulkan "Aman, bukan limit".
Itu jawaban dari KONFIGURASI, bukan dari kenyataan.

Yang diuji:
  A. `FallbackLLM.health()` melaporkan provider yang sedang istirahat + error.
  B. `model_status` menyertakan bagian status provider.
  C. Instruksi internal ke mulut diberi penanda agar tidak dibacakan.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as M                                                # noqa: E402
from core.custom_brain import FallbackLLM                        # noqa: E402

ERRORS = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        ERRORS.append(name)


# ── A. health() ───────────────────────────────────────────────────────────
check("FallbackLLM punya health()", hasattr(FallbackLLM, "health"), "tidak ada")

if hasattr(FallbackLLM, "health"):
    class _Boom:
        model = "claude-opus-5.5"

        def complete(self, messages, tools=None, timeout=120):
            raise RuntimeError('HTTP 429: {"type":"FreeUsageLimitError"}')

    class _Good:
        model = "glm-5.3-flash"

        def complete(self, messages, tools=None, timeout=120):
            return "halo", []

    fb = FallbackLLM([("flagship", _Boom()), ("river", _Good())])
    text, _ = fb.complete([{"role": "user", "content": "hai"}], None, 30)
    h = fb.health()
    by = {x["pid"]: x for x in h}
    check("rotasi tetap berjalan", text == "halo", str(text))
    check("provider gagal dilaporkan ISTIRAHAT",
          by["flagship"]["resting_s"] > 0, str(by["flagship"]["resting_s"]))
    check("alasan kegagalan ikut dilaporkan (429)",
          "429" in by["flagship"]["error"], by["flagship"]["error"][:40])
    check("provider sehat tidak istirahat",
          by["river"]["resting_s"] == 0, str(by["river"]["resting_s"]))
    check("model ikut dilaporkan",
          by["flagship"]["model"] == "claude-opus-5.5", by["flagship"]["model"])

# ── B. model_status menyebut status provider ──────────────────────────────
SRC = (ROOT / "main.py").read_text(encoding="utf-8")
check("model_status memakai health()", ".health()" in SRC)
check("model_status menyebut 'istirahat'",
      "sedang istirahat" in SRC or "Provider sedang istirahat" in SRC)
check("model_status menyebut 'Semua provider' saat sehat",
      "Semua provider" in SRC)

# ── C. instruksi internal tidak dibacakan ─────────────────────────────────
check("ada penanda instruksi internal", "INSTRUKSI UNTUKMU" in SRC)
check("prompt mulut melarang membacakan penanda itu",
      "INSTRUKSI UNTUKMU" in M._voice_interface_prompt("JARVIS"))

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
