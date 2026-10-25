# -*- coding: utf-8 -*-
"""Probe: perakitan konteks — apa yang benar-benar dikirim ke model.

Gejala yang dilaporkan: JARVIS melompat ke topik yang tidak diminta
("proyek Hermes"), menawarkan topik lain di akhir jawaban, dan memanggil tool
yang sama berulang. Uji ini mengunci penyebabnya supaya tidak kembali:

  1. `projects.current_project` TIDAK ikut di prompt kecuali pengguna
     membicarakan proyek.
  2. Ingatan semantik hanya masuk kalau skornya cukup.
  3. Tool MCP tidak dikirim ke sesi suara secara bawaan (207 tool ≈ 400 KB).
  4. Tool yang terdaftar di registri tidak dimintai izin (bukan "unknown").
  5. Blok [ATURAN KONTEKS] ada dan memuat larangan menawarkan topik.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


import main                                                    # noqa: E402
from core.agent import permissions                             # noqa: E402
from core import semantic                                      # noqa: E402

# ── 1. memori: agenda basi tidak ikut ───────────────────────────────────────
mem = {"identity": {"name": {"value": "Raja"}},
       "projects": {"current_project": {"value": "using AI agent in Hermes"}},
       "preferences": {"language": {"value": "Indonesian"}}}

p_weather = main._memory_for_prompt(mem, "tolong tampilkan cuaca hari ini")
check("proyek TIDAK ikut saat bicara cuaca", "Hermes" not in p_weather)
check("identitas tetap ikut", "Raja" in p_weather)

p_proj = main._memory_for_prompt(mem, "lanjutkan proyek saya yang kemarin")
check("proyek ikut saat bicara proyek", "Hermes" in p_proj)

p_empty = main._memory_for_prompt(mem, "")
check("tanpa ucapan → proyek tidak ikut", "Hermes" not in p_empty)

# ── 2. ingatan semantik: ambang skor ────────────────────────────────────────
semantic.PATH = Path(tempfile.mkdtemp()) / "s.jsonl"
semantic.add("Bos suka laporan singkat")
semantic.add("Proyek toko memakai folder D:/toko")
check("tanpa ambang → ada hasil", bool(semantic.context("proyek toko", k=2)))
check("ambang tinggi → tersaring",
      semantic.context("cuaca besok pagi", k=2, min_score=1.5) == "")

# ── 3. MCP tidak dikirim ke sesi suara secara bawaan ────────────────────────
import json as _json                                          # noqa: E402
cfg = _json.loads((Path("config") / "api_keys.json").read_text(encoding="utf-8"))
check("mcp_voice_tools bawaan mati", cfg.get("mcp_voice_tools") in (None, False))

# ── 4. izin: tool terdaftar tidak dianggap asing ────────────────────────────
check("browser_control terdaftar → allow",
      permissions.decide("browser_control", cfg, known=True)[0] == "allow")
check("composio_connector terdaftar → allow",
      permissions.decide("composio_connector", cfg, known=True)[0] == "allow")
check("tool asing tetap confirm",
      permissions.decide("tool_karangan", cfg, known=False)[0] == "confirm")
check("kirim keluar tetap confirm",
      permissions.decide("send_message", cfg, known=True)[0] == "confirm")
check("klik layar tetap confirm",
      permissions.decide("screen_agent", cfg, known=True)[0] == "confirm")

# ── 5. aturan konteks ada di kode prompt ────────────────────────────────────
src = Path("main.py").read_text(encoding="utf-8")
check("blok ATURAN KONTEKS ada", "[ATURAN KONTEKS]" in src)
check("larangan menawarkan topik", "Jangan menawarkan topik" in src)
check("larangan mengulang tool", "dua kali untuk pertanyaan" in src)
check("memori disebut referensi", "REFERENSI, bukan agenda" in src)

for e in errs:
    print("  ERROR:", e)
print("ERRORS:", errs if errs else "none")
