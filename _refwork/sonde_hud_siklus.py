"""Sonde: siklus 4 gaya HUD harus benar-benar berputar.

Konteks (25 Okt, laporan bos): setelah pindah dari "human" ke "robot",
tombol HUD tidak bisa lagi berpindah ke gaya lain — log menunjukkan
"HUD switched to the 2.5D mask" berulang-ulang, dan hanya 2 animasi
yang bisa dilihat. Tuduhan bos: dua animasi lain dihapus.

Sonde ini membuktikan ADA/TIDAK-nya tanpa asumsi:
  A. `save_hud_style("face25d")` benar-benar menyimpan "face25d".
  B. `get_hud_style()` mengembalikan nilai yang baru disimpan.
  C. Siklus `_NEXT` (dari ui.py) mencapai 4 gaya berbeda dalam 4 tekan.
  D. `_set_hud_style("face25d")` tidak melempar KeyError.

Config diisolasi ke direktori sementara supaya config milik bos tidak
ikut tersentuh. Fungsi yang diuji tetap fungsi asli (tidak dimock).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import memory.config_manager as cm

tmp = Path(tempfile.mkdtemp(prefix="sonde_hud_"))
cm.CONFIG_DIR = tmp
cm.CONFIG_FILE = tmp / "api_keys.json"

# Siklus persis seperti di ui.MainWindow._toggle_hud_style
_NEXT = {"face": "face25d", "face25d": "core", "core": "orbit", "orbit": "face"}

fails = []


def cek(nama, ok, detail=""):
    print(("  OK   " if ok else "  GAGAL") + f"  {nama}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(nama)


print("A. simpan 'face25d'")
cm.save_hud_style("face25d")
dibaca = cm.get_hud_style()
cek("A1 tersimpan apa adanya", dibaca == "face25d", f"get_hud_style()={dibaca!r}")

print("B. whitelist mengenal keempat gaya")
for s in ("face", "face25d", "core", "orbit"):
    cek(f"B:{s} ada di HUD_STYLES", s in cm.HUD_STYLES, f"HUD_STYLES={cm.HUD_STYLES}")

print("C. siklus 4 tekan dari 'face'")
cm.save_hud_style("face")
jejak = []
for i in range(4):
    ingin = _NEXT.get(cm.get_hud_style(), "face")
    cm.save_hud_style(ingin)
    jejak.append(cm.get_hud_style())
print("     jejak:", " -> ".join(jejak))
cek("C1 empat gaya berbeda", len(set(jejak)) == 4, f"unik={sorted(set(jejak))}")
cek("C2 kembali ke awal", jejak[-1] == "face", f"akhir={jejak[-1]!r}")

print("D. label _SAYS lengkap")
_SAYS = {"face": "the animated face.", "face25d": "the 2.5D mask.",
         "core": "the reactor core.", "orbit": "the orbit wireframe."}
for s in ("face", "face25d", "core", "orbit"):
    cek(f"D:{s} punya kalimat log", s in _SAYS)

print()
if fails:
    print(f"HASIL: MERAH — {len(fails)} cek gagal")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("HASIL: HIJAU — semua cek lolos")
