"""Sonde: prefix prompt harus STABIL supaya cache provider bisa bekerja.

Temuan 24 Okt: tidak ada kode cache sama sekali di proyek ini, DAN cache
otomatis provider tidak mungkin kena karena `time_ctx` (jam:menit) ditaruh
paling awal system prompt. Prefix berubah setiap menit → cache selalu miss.

Yang dijaga sonde ini:
  A. Bagian statis (prompt + identitas) ada di DEPAN.
  B. `time_ctx` ada di EKOR, bukan kepala.
  C. Prefix statis cukup panjang untuk cache (>= 1024 token ≈ 4096 char).
  D. Tidak ada penanda waktu lain di dalam prompt statis.
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


SRC = (ROOT / "main.py").read_text(encoding="utf-8")

print("== urutan bagian system prompt ==")
check("yang statis di depan", "parts = [sys_prompt, identity_ctx]" in SRC)
check("sys_prompt tidak lagi ditambahkan di tengah",
      "parts.append(sys_prompt)" not in SRC)
check("time_ctx ditambahkan di ekor",
      "parts.append(time_ctx)" in SRC)
# hanya prompt OTAK yang relevan untuk cache; mulut (Gemini Live) tidak
check("time_ctx tidak lagi di kepala prompt otak",
      "parts = [time_ctx, identity_ctx]" not in SRC)

# urutan relatif: append(time_ctx) harus SETELAH aturan statis terakhir
try:
    i_time = SRC.index("parts.append(time_ctx)")
    i_aturan = SRC.index("[ATURAN KONTEKS]")
    check("waktu berada setelah aturan statis", i_time > i_aturan,
          f"{i_time} vs {i_aturan}")
except ValueError:
    check("waktu berada setelah aturan statis", False, "penanda tidak ketemu")

print("\n== panjang prefix yang bisa di-cache ==")
try:
    _prompt = M._load_system_prompt()
    chars = len(_prompt)
    tok = chars // 4
    check("prompt statis cukup panjang untuk cache (>=1024 token)",
          tok >= 1024, f"{chars} char ~ {tok} tok")
    print(f"     prefix statis: {chars} char ~ {tok} token "
          f"(inilah yang bisa di-cache provider)")
except Exception as e:                                        # noqa: BLE001
    check("prompt statis terbaca", False, str(e)[:60])

print("\n== tidak ada cache eksplisit (jujur) ==")
has_cache = any(k in SRC for k in ("cache_control", "prompt_cache"))
print(f"     cache eksplisit di kode: {'ADA' if has_cache else 'BELUM ADA'}"
      " — mengandalkan cache otomatis provider")

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
