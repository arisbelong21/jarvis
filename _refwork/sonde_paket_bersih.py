"""Sonde: paket ZIP yang dijual tidak boleh membawa data/kunci milik pembuat.

Memeriksa ISI ZIP, bukan niat skrip pembangunnya.
Dijalankan SEBELUM build_package.py ada -> MERAH.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZIP = ROOT / "dist" / "JARVIS-SHARE.zip"

ERRORS: list[str] = []
WARN: list[str] = []
OK = 0


def check(name: str, got, want) -> None:
    global OK
    if got == want:
        OK += 1
        print(f"  OK   {name}")
    else:
        ERRORS.append(name)
        print(f"  FAIL {name}: dapat {got!r}, harusnya {want!r}")


if not ZIP.is_file():
    print("  FATAL: paket belum dibangun ->", ZIP)
    sys.exit(2)

zf = zipfile.ZipFile(ZIP)
_RAW = zf.namelist()
# Semua entri harus berada di bawah SATU folder induk (kalau tidak, pembeli
# menumpahkan 10 folder ke tempat ekstrak).
PREFIX = "JARVIS-SHARE/"
check_prefixed = all(n.startswith(PREFIX) for n in _RAW)
names = [n[len(PREFIX):] if n.startswith(PREFIX) else n for n in _RAW]

print("== 0. struktur zip ==")
check("semua entri di bawah satu folder induk", check_prefixed, True)

print("\n== 1. tidak ada berkas terlarang ==")
BAD_SUFFIX = (".pyc", ".pyo", ".bak", ".pem", ".key", ".log")
BAD_PARTS = ("__pycache__", ".venv313", "_refwork", ".workbuddy-ai",
             "logs/", "cloud_state", "dist/", "certs/", "desktop/")
bad = [n for n in names if n.lower().endswith(BAD_SUFFIX)
       or any(p in n for p in BAD_PARTS)]
check("tidak ada .pyc/.bak/.pem/.key/.log", bad, [])
check("tidak ada folder kerja pribadi",
      [n for n in names if n.startswith(("_refwork/", ".workbuddy-ai/",
                                         "logs/", "desktop/"))], [])

print("\n== 2. data pengguna tidak ikut ==")
mem_data = [n for n in names
            if n.startswith("memory/") and not n.endswith(".py")]
check("memory/ hanya berisi kode", mem_data, [])
check("tidak ada long_term.json", "memory/long_term.json" in names, False)
check("tidak ada audit.jsonl", "memory/audit.jsonl" in names, False)

print("\n== 3. kunci API kosong ==")
cfg = json.loads(zf.read(PREFIX + "config/api_keys.json").decode("utf-8"))
check("gemini_api_key kosong", cfg.get("gemini_api_key"), "")
prov_filled = [pid for pid, p in (cfg.get("providers") or {}).items()
               if isinstance(p, dict) and p.get("api_key")]
check("semua provider api_key kosong", prov_filled, [])
pc = cfg.get("plugin_config") or {}
check("telegram bot_token kosong",
      (pc.get("telegram_remote") or {}).get("bot_token"), "")
check("telegram allowed_chat_ids kosong",
      (pc.get("telegram_remote") or {}).get("allowed_chat_ids"), [])
check("composio api_key kosong", (pc.get("composio") or {}).get("api_key"), "")
check("calendar ics_url kosong", (pc.get("calendar") or {}).get("ics_url"), "")

print("\n== 4. bawaan cocok untuk instalasi baru ==")
check("voice_brain = live (belum punya provider)", cfg.get("voice_brain"),
      "live")
check("active_provider kosong", cfg.get("active_provider"), "")
check("user_name kosong", cfg.get("user_name"), "")
check("perangkat audio otomatis", cfg.get("input_device"), "")
check("telegram tidak auto-start",
      (pc.get("telegram_remote") or {}).get("start_on_launch"), False)

print("\n== 5. MCP tidak menunjuk path mesin pembuat ==")
mcp = json.loads(zf.read(PREFIX + "config/mcp.json").decode("utf-8"))
rea = (mcp.get("servers") or {}).get("rea") or {}
check("server rea dimatikan", rea.get("enabled"), False)
check("env rea dikosongkan", rea.get("env"), {})
check("tidak ada path C:\\rea-tools",
      "rea-tools" in zf.read(PREFIX + "config/mcp.json").decode("utf-8"), False)

print("\n== 6. berkas wajib ada ==")
for need in ("main.py", "ui.py", "requirements.txt", "run_aris_console.bat",
             "setup.bat", "start.bat", "README-PEMBELI.txt",
             "core/prompt.txt", "config/api_keys.json"):
    check(f"ada {need}", need in names, True)

print("\n== 7. tidak ada jejak pribadi di isi berkas ==")
MARKERS = ("Kumaran", "ASROCK B650M", "7152303949", "arischopper",
           "ariscoper", "rea-tools")
hits: list[str] = []
for n in names:
    if not n.endswith((".py", ".json", ".txt", ".bat", ".md", ".html", ".js")):
        continue
    try:
        body = zf.read(PREFIX + n).decode("utf-8", "ignore")
    except Exception:                                   # noqa: BLE001
        continue
    for m in MARKERS:
        if m in body:
            hits.append(f"{n}:{m}")
check("tidak ada penanda pribadi", hits, [])

# Persona, bukan data pribadi: dilaporkan, tidak menggagalkan paket.
raja = [n for n in names
        if n.endswith(".py")
        and "raja" in zf.read(PREFIX + n).decode("utf-8", "ignore").lower()]
if raja:
    WARN.append("sapaan 'raja' masih ada di: " + ", ".join(raja[:4]))

zf.close()
print()
for w in WARN:
    print("  WARN", w)
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
