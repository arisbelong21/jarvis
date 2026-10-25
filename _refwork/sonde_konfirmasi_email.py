"""sonde_konfirmasi_email.py — gerbang konfirmasi email harus cukup SEKALI tanya.

Gejala yang dilaporkan bos + bukti audit (22 Okt 05:28-05:29): 8 permintaan
konfirmasi dalam ~70 detik untuk gmail_send yang sama — penerima & subjek
identik, hanya body yang ditulis ulang model tiap giliran, sehingga hash
seluruh argumen tidak pernah cocok dan "ya" tidak pernah dianggap sah.

Yang diuji:
  1. panggilan pertama selalu meminta konfirmasi;
  2. "ya" dengan body BERBEDA tetap konfirmasi yang sama, dan yang dieksekusi
     adalah payload yang tadi disetujui (bukan body hasil regenerasi);
  3. penerima berubah -> TIDAK boleh lolos memakai izin lama;
  4. konfirmasi kedaluwarsa (TTL) -> minta ulang.

Jalankan: .venv313\\Scripts\\python.exe _refwork/sonde_konfirmasi_email.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins import composio_actions as ca   # noqa: E402

SLUG = "GMAIL_SEND_EMAIL"
errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


base = {"recipient_email": "aris@example.org", "subject": "kabar",
        "body": "Halo A"}

# ── 1. panggilan pertama meminta konfirmasi ─────────────────────────────────
ca._pending.clear()
ok1, msg1 = ca._gate("gmail_send", dict(base), SLUG, dict(base))
check("panggilan pertama minta konfirmasi",
      ok1 is False and "KONFIRMASI" in msg1, msg1[:48])

# ── 2. body ditulis ulang model -> tetap satu konfirmasi ────────────────────
args2 = dict(base)
args2["body"] = "Halo B (ditulis ulang model)"
params2 = dict(args2)
params2["confirm"] = "ya"
ok2, msg2 = ca._gate("gmail_send", params2, SLUG, args2)
check("body berubah tetap dianggap konfirmasi yang sama",
      ok2 is True, msg2[:60])
check("yang dieksekusi = body yang disetujui",
      args2.get("body") == "Halo A", str(args2.get("body"))[:40])

# ── 3. penerima berubah tidak boleh menumpang izin lama ─────────────────────
ca._pending.clear()
ca._gate("gmail_send", dict(base), SLUG, dict(base))          # izin utk A
argsB = dict(base)
argsB["recipient_email"] = "lain@example.org"
paramsB = dict(argsB)
paramsB["confirm"] = "ya"
okB, msgB = ca._gate("gmail_send", paramsB, SLUG, argsB)
check("penerima berubah -> tetap minta konfirmasi", okB is False, msgB[:60])
argsA = dict(base)
paramsA = dict(argsA)
paramsA["confirm"] = "ya"
okA, msgA = ca._gate("gmail_send", paramsA, SLUG, argsA)
check("penerima asli tetap bisa dikonfirmasi", okA is True, msgA[:48])

# ── 4. konfirmasi kedaluwarsa ───────────────────────────────────────────────
ca._pending.clear()
ca._gate("gmail_send", dict(base), SLUG, dict(base))
for _k, _v in list(ca._pending.items()):
    ca._pending[_k] = (time.time() - 1, _v[1])                # tuakan 1 detik
argsT = dict(base)
paramsT = dict(argsT)
paramsT["confirm"] = "ya"
okT, msgT = ca._gate("gmail_send", paramsT, SLUG, argsT)
check("konfirmasi kedaluwarsa -> minta ulang", okT is False, msgT[:60])

print("ERRORS:", errs if errs else "none")
