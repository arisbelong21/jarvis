"""Sonde: perbaikan temuan audit K1 (memori atomik), K2 (izin), K3 (sandbox).

Dijalankan SEBELUM perbaikan -> harus MERAH.
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ERRORS: list[str] = []
OK = 0


def check(name: str, got, want) -> None:
    global OK
    if got == want:
        OK += 1
        print(f"  OK   {name}")
    else:
        ERRORS.append(name)
        print(f"  FAIL {name}: dapat {got!r}, harusnya {want!r}")


print("== K1. penulisan memori atomik ==")
from memory import memory_manager as mm

check("ada helper _atomic_write", hasattr(mm, "_atomic_write"), True)

tmpdir = Path(tempfile.mkdtemp())
mem_path = tmpdir / "long_term.json"
_orig = mm.MEMORY_PATH
mm.MEMORY_PATH = mem_path

if hasattr(mm, "_atomic_write"):
    mm._atomic_write(mem_path, {"identity": {"name": {"value": "Aris"}}})
    check("berkas tertulis & valid JSON",
          json.loads(mem_path.read_text(encoding="utf-8"))["identity"]["name"]["value"],
          "Aris")
    check("tidak meninggalkan .tmp",
          list(tmpdir.glob("*.tmp")), [])

print("\n== K1b. berkas rusak tidak menghapus ingatan ==")
# Siapkan: .bak berisi data, berkas utama rusak
(mem_path.parent / "long_term.json.bak").write_text(
    json.dumps({"identity": {"name": {"value": "Cadangan"}}}), encoding="utf-8")
mem_path.write_text('{"identity": {"name": {"value": "rus', encoding="utf-8")

loaded = mm.load_memory()
check("memori dipulihkan dari .bak",
      (loaded.get("identity") or {}).get("name", {}).get("value"), "Cadangan")
check("berkas rusak disimpan sebagai .corrupt",
      len(list(tmpdir.glob("*corrupt*"))) >= 1, True)

mm.MEMORY_PATH = _orig

print("\n== K2. bypass izin hanya untuk computer_control ==")
from core.agent import permissions as P

check("desktop_control dari otak -> confirm",
      P.decide("desktop_control", {}, args={"action": "task"},
               internal=True)[0], "confirm")
check("screen_agent dari otak -> confirm",
      P.decide("screen_agent", {}, args={}, internal=True)[0], "confirm")
check("computer_control type dari otak -> allow",
      P.decide("computer_control", {}, args={"action": "type"},
               internal=True)[0], "allow")
check("computer_control wait -> allow",
      P.decide("computer_control", {}, args={"action": "wait"})[0], "allow")
check("desktop_control dari Gemini -> confirm",
      P.decide("desktop_control", {}, args={"action": "task"})[0], "confirm")

print("\n== K3. sandbox tidak mengekspos jalan keluar ==")
import actions.desktop as D
importlib.reload(D)
sb = D._build_sandbox()
check("ctypes TIDAK diekspos", "ctypes" in sb, False)
check("getattr TIDAK diekspos", "getattr" in (sb.get("__builtins__") or {}), False)
check("pyautogui tetap ada (fitur desktop)",
      "pyautogui" in sb or not D._PYAUTOGUI, True)
check("Path tetap ada (fitur berkas desktop)", "Path" in sb, True)


print("\n== K1c. pemulihan BERTAHAN (audit kedua) ==")
tmp2 = Path(tempfile.mkdtemp()); mp2 = tmp2 / "long_term.json"
mm.MEMORY_PATH = mp2
(tmp2 / "long_term.json.bak").write_text(
    json.dumps({"identity": {"name": {"value": "Cadangan2"}}}), encoding="utf-8")
mp2.write_text('{"identity": {"name": {"value": "rus', encoding="utf-8")

vals = []
for _ in range(3):
    vals.append((mm.load_memory().get("identity") or {}).get("name", {}).get("value"))
check("panggilan ke-2 & ke-3 tetap berisi", vals[1:], ["Cadangan2", "Cadangan2"])
check("berkas utama dibuat ulang setelah pemulihan", mp2.exists(), True)
check("cadangan tidak tertimpa saat pemulihan",
      json.loads((tmp2 / "long_term.json.bak").read_text(encoding="utf-8"))
      ["identity"]["name"]["value"], "Cadangan2")
check("tidak ada .tmp tertinggal", list(tmp2.glob("*.tmp")), [])
mm.MEMORY_PATH = _orig

print("\n== K1d. config juga atomik (audit kedua) ==")
from memory import config_manager as cm
_src_cm = (ROOT / "memory" / "config_manager.py").read_text(encoding="utf-8")
check("tidak ada CONFIG_FILE.write_text tersisa",
      "CONFIG_FILE.write_text" in _src_cm, False)
check("ada _atomic_write_json", hasattr(cm, "_atomic_write_json"), True)

_orig_cfg = cm.CONFIG_FILE
tmp3 = Path(tempfile.mkdtemp()); cfgp = tmp3 / "api_keys.json"
cm.CONFIG_FILE = cfgp
if hasattr(cm, "_atomic_write_json"):
    cm._atomic_write_json(cfgp, {"gemini_api_key": "abc", "voice_brain": "live"})
    check("config tertulis & valid",
          json.loads(cfgp.read_text(encoding="utf-8"))["voice_brain"], "live")
    check("tidak ada .tmp config", list(tmp3.glob("*.tmp")), [])

# config rusak -> disimpan, tidak ditimpa
(cfgp.parent / "api_keys.json.bak").write_text(
    json.dumps({"gemini_api_key": "cadangan", "voice_brain": "live"}),
    encoding="utf-8")
cfgp.write_text('{"gemini_api_key": "rus', encoding="utf-8")
got = cm.load_api_keys()
check("config dipulihkan dari .bak", got.get("gemini_api_key"), "cadangan")
check("config rusak disimpan sebagai .corrupt",
      len(list(tmp3.glob("*corrupt*"))) >= 1, True)
cm.CONFIG_FILE = _orig_cfg


print("\n== A4. UI tidak boleh menulis config langsung (audit ketiga) ==")
_src_ui = (ROOT / "ui.py").read_text(encoding="utf-8")
check("tidak ada API_FILE.write_text di ui.py",
      "API_FILE.write_text" in _src_ui, False)
check("setup memakai jalur atomik",
      "_save_cfg(data)" in _src_ui, True)
check("setup TIDAK mengganti config dengan 2 field",
      'json.dumps({"gemini_api_key": key' in _src_ui, False)
check("setup menggabungkan ke config lama",
      'data["gemini_api_key"] = key' in _src_ui, True)
check("config_manager punya save_config publik",
      hasattr(cm, "save_config"), True)

# save_config harus mempertahankan field lain
tmp4 = Path(tempfile.mkdtemp()); cfg4 = tmp4 / "api_keys.json"
cm.CONFIG_FILE = cfg4
cm.save_config({"gemini_api_key": "k1", "voice_brain": "live",
                "providers": {"river": {"api_key": "r1"}}})
cm.save_config({"gemini_api_key": "k2"})          # hanya ganti kunci
_after = json.loads(cfg4.read_text(encoding="utf-8"))
check("kunci berganti", _after.get("gemini_api_key"), "k2")
cm.CONFIG_FILE = _orig_cfg


print("\n== A5. penulisan bersamaan tidak merusak berkas (audit ketiga) ==")
import threading
tmp5 = Path(tempfile.mkdtemp()); cfg5 = tmp5 / "api_keys.json"
cm.CONFIG_FILE = cfg5
_errs: list[str] = []


def _hammer(i: int) -> None:
    try:
        for _ in range(10):
            cm.save_config({"gemini_api_key": f"k{i}", "pad": "x" * 1500})
    except Exception as e:                       # noqa: BLE001
        _errs.append(repr(e))


_ts = [threading.Thread(target=_hammer, args=(i,)) for i in range(4)]
for t in _ts:
    t.start()
for t in _ts:
    t.join()
check("config: tidak ada exception saat bersamaan", _errs, [])
check("config: JSON tetap valid",
      isinstance(json.loads(cfg5.read_text(encoding="utf-8")), dict), True)
check("config: tidak ada .tmp tertinggal", list(tmp5.glob("*.tmp")), [])

tmp6 = Path(tempfile.mkdtemp()); mp6 = tmp6 / "long_term.json"
mm.MEMORY_PATH = mp6
_errs2: list[str] = []


def _hammer_mem(i: int) -> None:
    try:
        for _ in range(10):
            mm.save_memory({"identity": {"name": {"value": f"n{i}"}},
                            "notes": {}, "preferences": {}, "projects": {},
                            "relationships": {}, "wishes": {}})
    except Exception as e:                       # noqa: BLE001
        _errs2.append(repr(e))


_ts2 = [threading.Thread(target=_hammer_mem, args=(i,)) for i in range(4)]
for t in _ts2:
    t.start()
for t in _ts2:
    t.join()
check("memori: tidak ada exception saat bersamaan", _errs2, [])
check("memori: JSON tetap valid",
      isinstance(json.loads(mp6.read_text(encoding="utf-8")), dict), True)
cm.CONFIG_FILE = _orig_cfg
mm.MEMORY_PATH = _orig

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
