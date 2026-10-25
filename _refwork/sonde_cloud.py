# -*- coding: utf-8 -*-
"""Sonde cloud — modul cloud harus hidup di core/, tempat plugin mencarinya.

MERAH sebelum perbaikan: cloud.py berada di ROOT proyek sementara
plugins/cloud_agent.py melakukan `from core import cloud` di tiga tempat —
jadi cloud_agent selalu menjawab "core/cloud.py is missing from this install".
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


print("== lokasi modul ==")
check("core/cloud.py ada", (ROOT / "core" / "cloud.py").is_file())
check("cloud.py tidak lagi di root (tidak dobel)",
      not (ROOT / "cloud.py").is_file())

print("== impor yang dipakai plugin ==")
try:
    from core import cloud
    ok_import = True
except Exception as e:                                   # noqa: BLE001
    cloud, ok_import = None, False
    print(f"  impor gagal: {type(e).__name__}: {e}")
check("from core import cloud berhasil", ok_import)
if ok_import:
    check("nama modul core.cloud", cloud.__name__ == "core.cloud", cloud.__name__)
    for attr in ("Client", "make_pair_code", "parse_pair_code", "new_secret"):
        check(f"punya {attr}", hasattr(cloud, attr))

print("== cloud_agent tidak lagi melaporkan modul hilang ==")
try:
    from plugins import cloud_agent
    _ok, _msg = cloud_agent._test({})
    check("_test() tidak mengembalikan pesan 'missing'",
          "missing from this install" not in str(_msg), str(_msg)[:60])
except Exception as e:                                   # noqa: BLE001
    check("cloud_agent bisa diimpor", False, f"{type(e).__name__}: {e}")

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
