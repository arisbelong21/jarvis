"""Sonde: satu tabel kasus untuk SEMUA keputusan giliran.

Delapan gerbang lama diuji terpisah-pisah, jadi tabrakannya tidak pernah
terlihat. Di sini semuanya diuji sebagai satu tabel — termasuk kasus yang
dulu salah: jawaban "pakai otak custom saja" (yang juga cocok dengan perintah
mode global) harus dibaca sebagai jawaban tawaran.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.decide import (decide_turn, SMALL, MODE, OFFER_REPLY,          # noqa: E402
                         FORCE_BRAIN, BRAIN, PASS)

ERRORS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'OK  ' if ok else 'FAIL'} {name:<52} -> {got}"
          + ("" if ok else f"  (harus {want})"))
    if not ok:
        ERRORS.append(name)


print("== tawaran menang atas perintah mode (bug v18) ==")
check("jawaban tawaran 'pakai otak custom saja'",
      decide_turn("pakai otak custom saja", name="brain", offer_fresh=True,
                  offer_choice="brain", mode_command="force")["action"],
      OFFER_REPLY)
check("jawaban tawaran 'kamu saja'",
      decide_turn("kamu saja", name="brain", offer_fresh=True,
                  offer_choice="gemini")["action"], OFFER_REPLY)

print("\n== perintah kendali mode ==")
check("'alihkan ke custom' tanpa tawaran",
      decide_turn("alihkan pekerjaan ke custom", name="brain",
                  mode_command="force")["action"], MODE)
check("'mode default'",
      decide_turn("mode default", name="brain",
                  mode_command="default")["action"], MODE)

print("\n== mode paksa ==")
check("tool ringan saat paksa -> otak",
      decide_turn("buka youtube", name="youtube_video",
                  force_brain=True)["action"], FORCE_BRAIN)
check("builtin saat paksa -> tetap langsung",
      decide_turn("ingat namaku", name="recall_memory", is_builtin=True,
                  force_brain=True)["action"], PASS)

print("\n== obrolan ringan ==")
check("'habis limitnya' -> mulut jawab sendiri",
      decide_turn("habis limitnya", name="brain", small_turn=True)["action"],
      SMALL)
check("tool ringan tetap boleh jalan",
      decide_turn("buka youtube", name="youtube_video",
                  small_turn=False)["action"], PASS)

print("\n== permintaan ke otak ==")
check("pertanyaan biasa ke otak",
      decide_turn("model apa yang dipakai?", name="brain")["action"], BRAIN)
check("niat menulis pada tool ringan -> otak",
      decide_turn("buatkan file", name="open_app", write_intent=True)["action"],
      BRAIN)
check("niat menulis tapi Gemini diizinkan -> langsung",
      decide_turn("buatkan file", name="open_app", write_intent=True,
                  gemini_allowed=True)["action"], PASS)

print("\n== penjaga ==")
check("otak sedang bekerja -> jangan menumpuk",
      decide_turn("halo", name="brain", in_brain=True)["action"], PASS)
check("panggilan internal -> jangan diputuskan",
      decide_turn("x", name="brain", internal=True)["action"], PASS)


print("\n== sambungan di main.py (konsolidasi benar-benar dipakai) ==")
SRC = (ROOT / "main.py").read_text(encoding="utf-8")
check("main mengimpor decide_turn", "from core.decide import" in SRC, True)
check("satu titik dispatch mencetak putusan",
      'putusan: {_dec[' in SRC, True)
check("gerbang mode lama tidak lagi ganda",
      SRC.count('if _cmd == "force"') == 1, True)
check("gerbang obrolan lama tidak lagi ganda",
      SRC.count("if (_is_small_turn(utterance)") == 0, True)
check("notifikasi error otak aktif secara bawaan",
      'get("voice.provider_notice", True)' in SRC, True)
check("notifikasi diucapkan mulut, bukan otak",
      "plugin_say(" in SRC.split("def _on_brain_fallback")[1][:2000], True)

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
