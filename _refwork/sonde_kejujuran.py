# -*- coding: utf-8 -*-
"""Sonde kejujuran — tool tidak boleh mengaku "terkirim" tanpa bukti.

Mengunci dua hal:
  1. send_message: jalur desktop yang hanya menekan tombol (Telegram, Signal,
     Discord, Instagram, Messenger) TIDAK boleh mengembalikan "Message sent".
     Hanya jalur WhatsApp yang punya driver pembaca-balik yang boleh.
  2. switch_engine: mode mesin bisa diganti tanpa menyunting config manual.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


# ── 1. send_message: klaim buta ─────────────────────────────────────────────
print("== send_message ==")
from actions import send_message as sm

src = (ROOT / "actions" / "send_message.py").read_text(encoding="utf-8")
check("ada pembangun kalimat jujur (_blind_result)", hasattr(sm, "_blind_result"))

kalimat = sm._blind_result("Telegram", "Budi")
check("kalimat jujur: menyebut tidak bisa konfirmasi",
      "could NOT confirm" in kalimat, kalimat[:70])
check("kalimat jujur: TIDAK mengaku 'Message sent'",
      "Message sent" not in kalimat)

# hanya jalur WhatsApp (terverifikasi) yang boleh mengaku terkirim
n_sent_claims = src.count("Message sent to")
check("hanya 1 klaim 'Message sent' (jalur WhatsApp terverifikasi)",
      n_sent_claims == 1, f"{n_sent_claims} tempat")
check("klaim itu milik WhatsApp",
      "Message sent to {receiver} via WhatsApp." in src)
for platform in ("Instagram", "Messenger"):
    check(f"{platform}: memakai _blind_result",
          f'_blind_result("{platform}", receiver)' in src)

# ── 2. switch_engine: mode mesin bisa diganti ───────────────────────────────
print("== switch_engine ==")
from memory import config_manager as cm

check("config_manager punya set_voice_brain", hasattr(cm, "set_voice_brain"))
try:
    cm.set_voice_brain("ngawur")
    check("mode tak dikenal ditolak", False, "tidak melempar")
except ValueError:
    check("mode tak dikenal ditolak", True, "ValueError")

main_src = (ROOT / "main.py").read_text(encoding="utf-8")
decl_src = (ROOT / "core" / "tool_decls.py").read_text(encoding="utf-8")
check("tool switch_engine dideklarasikan (core/tool_decls.py)",
      '"name": "switch_engine"' in decl_src)
check("handler switch_engine memanggil set_voice_brain",
      "set_voice_brain(_mode)" in main_src)

# ── 3. code_helper: kode inline bisa dijalankan ─────────────────────────────
print("== code_helper ==")
from actions.code_helper import code_helper as _ch

_out = _ch({"action": "run", "code": "print('sonde-ok')",
            "language": "python", "timeout": 15})
check("run dengan kode inline berjalan", "sonde-ok" in str(_out), str(_out)[:40])
_out2 = _ch({"action": "run", "timeout": 5})
check("run tanpa apa pun memberi petunjuk, bukan jalan buntu",
      "pass `code`" in str(_out2), str(_out2)[:50])

# ── 4. log cadangan menyebut ALASAN kegagalan ───────────────────────────────
print("== log cadangan ==")
_brain_src = (ROOT / "core" / "custom_brain.py").read_text(encoding="utf-8")
check("pesan CADANGAN memuat alasan provider pertama gagal",
      "errors[0]" in _brain_src)

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
