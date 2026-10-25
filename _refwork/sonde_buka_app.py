# -*- coding: utf-8 -*-
"""Sonde open_app — nama aplikasi TIDAK boleh ditebak lewat substring.

MERAH terhadap kode SEKARANG: _normalize memakai `alias_key in key`, jadi
"opencode" mengandung "code" -> dipetakan ke "code" -> di mesin bos `code`
menunjuk ke shim Cursor (E:\\cursor\\...\\code.cmd lebih dulu di PATH
daripada VS Code asli). Hasilnya: minta Open Code, yang terbuka Cursor,
sementara open_app mengembalikan "Opened opencode." — klaim tanpa bukti.

Yang diuji: kode NYATA actions/open_app.py (impor murni Python, tanpa Qt).
Yang di-mock: TIDAK ADA. Launch tidak dijalankan — hanya pemetaan nama.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


try:
    from actions import open_app as oa
except Exception as e:                                   # noqa: BLE001
    print(f"  impor open_app: {type(e).__name__}: {e} => FAIL")
    raise SystemExit(1)

# ── 1. nama yang HARUS tidak tertukar ───────────────────────────────────────
print("== pemetaan nama ==")
_kasus = [
    # (yang diketik, yang TIDAK boleh muncul sebagai target)
    ("opencode", "code"),
    ("open code", "code"),
    ("opencode ai", "code"),
]
for raw, salah in _kasus:
    got = oa._normalize(raw)
    check(f"'{raw}' tidak dipetakan ke '{salah}'", got != salah, got)

# nama yang memang harus tetap bekerja
check("'vscode' -> code", oa._normalize("vscode") == "code", oa._normalize("vscode"))
check("'visual studio code' -> code",
      oa._normalize("visual studio code") == "code",
      oa._normalize("visual studio code"))
check("'chrome' -> chrome", oa._normalize("chrome") == "chrome",
      oa._normalize("chrome"))
check("'notepad' -> notepad.exe", oa._normalize("notepad") == "notepad.exe",
      oa._normalize("notepad"))
check("'cursor' -> cursor", oa._normalize("cursor") == "cursor",
      oa._normalize("cursor"))

# ── 2. alias eksplisit untuk aplikasi yang bos pakai ────────────────────────
print("== alias baru ==")
for nama in ("opencode", "cursor", "windsurf"):
    check(f"alias '{nama}' terdaftar", nama in oa._APP_ALIASES)

# ── 3. klaim hasil harus berbasis bukti, bukan kata yang diminta ────────────
print("== klaim hasil ==")
src = Path("actions/open_app.py").read_text(encoding="utf-8")
check("ada verifikasi jendela (_window_titles / _wait_new_window)",
      "_window_titles" in src and "_wait_new_window" in src)
check("hasil menyebut jendela yang terlihat",
      "window:" in src)
check("tidak lagi mengembalikan 'Opened {app_name}.' tanpa bukti",
      'return f"Opened {app_name}."' not in src)

# ── 4. pemilihan 'code' menghindari shim Cursor bila ada ────────────────────
check("ada pemilih executable yang melewati shim lain (_pick_executable)",
      "_pick_executable" in src)

# ── 5. penilaian jendela: pencarian web bukan keberhasilan ──────────────────
print("== penilaian jendela ==")
_kasus_jendela = [
    ("notepad", "Untitled - Notepad", "ok"),
    ("visual studio code", "main.py - Visual Studio Code", "ok"),
    ("chrome", "YouTube - Google Chrome", "ok"),
    ("opencode", "opencode - Windows Terminal", "ok"),
    ("zzz_no_such", "zzz_no_such - Search - Personal - Microsoft Edge", "search"),
    ("excel", "Untitled - Notepad", "other"),
]
for nama, judul, mau in _kasus_jendela:
    got = oa._judge_window(nama, judul)
    check(f"jendela '{judul[:32]}' -> {mau}", got == mau, got)

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
