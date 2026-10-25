# -*- coding: utf-8 -*-
"""Sonde baca — file besar bisa dibaca bertahap, tanpa berkas bantu.

MERAH sebelum perbaikan: `read_file` memotong di 4.000 karakter dengan
"[Truncated]" tanpa cara meminta sisanya, dan `code_helper` tidak mengenal
aksi `read_file` — file `core/cloud.py` (143.815 karakter) hanya terbaca 2,8%.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


isi = "".join(f"baris-{i:05d} abcdefghij\n" for i in range(440))
fd, tmp = tempfile.mkstemp(suffix=".py", text=True)
os.close(fd)
Path(tmp).write_text(isi, encoding="utf-8")
total = len(isi)

try:
    from actions.file_controller import read_file
    from actions.code_helper import code_helper

    print("== baca bertahap ==")
    a = read_file(tmp)
    check("bagian 1 menyebut offset berikutnya", "offset=4000" in a,
          a[-70:].replace("\n", " "))
    b = read_file(tmp, offset=4000)
    check("bagian 2 mulai dari 4000", "4000–8000" in b, b[-70:].replace("\n", " "))
    last = read_file(tmp, offset=(total // 4000) * 4000)
    check("bagian terakhir menyebut AKHIR file", "AKHIR file" in last,
          last[-70:].replace("\n", " "))

    print("== code_helper menerima read_file ==")
    r = code_helper({"action": "read_file", "file_path": tmp})
    check("aksi read_file tidak lagi 'unknown'",
          "unknown action" not in r.lower(), r[:60].replace("\n", " "))
    check("read_file menyebut jumlah baris", "lines)" in r, r[:70].replace("\n", " "))
    r2 = code_helper({"action": "read_file", "file_path": tmp,
                      "start": 100, "count": 3})
    check("read_file menghormati start/count", "baris-00099" in r2 or "baris-00100" in r2,
          r2[-80:].replace("\n", " "))

    print("== otak: potongan diberi tahu ==")
    brain = (ROOT / "core" / "custom_brain.py").read_text(encoding="utf-8")
    check("hasil terpotong diberi penanda DIPOTONG", "[DIPOTONG:" in brain)
    check("penanda menyuruh memakai offset", "offset" in brain and "berkas bantu" in brain)
    check("MAX_STEPS menyebut tugas belum selesai", "BELUM selesai" in brain)
    prompt = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
    check("prompt melarang berkas bantu di folder pengguna",
          "scratch files" in prompt and "temp folder" in prompt)
finally:
    try:
        os.unlink(tmp)
    except Exception:
        pass

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
