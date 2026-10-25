# -*- coding: utf-8 -*-
"""Probe Fase 4 Slice 2 — tanda terima milik kode + hasil mendarat sebagai fakta.

MERAH sebelum implementasi (harus gagal terhadap kode SEKARANG):
  1. config.get_flags() belum ada.
  2. core.receipt (compose_receipt, LandedRing, landed_line) belum ada.
  3. main.py belum menyuntik receipt [PEKERJAAN_DITERIMA], belum memakai
     FunctionResponse silent untuk tool latar, belum memakai cincin landed,
     belum memberi token {long_tools}, belum ada kalimat jembatan.
  4. core/prompt.txt belum punya carve-out ack untuk tool latar dan belum
     mendefinisikan tag [PEKERJAAN_DITERIMA].

Yang di-mock: TIDAK ADA. Semua fungsi diuji adalah fungsi nyata yang
diimpor dari modulnya; pemeriksaan struktur membaca berkas asli. Hanya
lokasi berkas flag yang ditukar ke direktori sementara (uji kill-switch),
bukan isi config/voice_flags.json yang asli.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")
PROMPT_SRC = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
PROMPT_FLAT = " ".join(PROMPT_SRC.split())   # teks ter-render, bukan bentuk baris

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


# ── 1. infra flag (config/voice_flags.json, default ON) ──────────────────────
try:
    import config as _config
    from config import VOICE_FLAG_DEFAULTS, get_flags
except Exception as e:                                   # noqa: BLE001
    print(f"  import config flags: {type(e).__name__}: {e} => FAIL")
    errs.append("import config flags")
else:
    check("7 flag ada di defaults",
          set(VOICE_FLAG_DEFAULTS) == {
              "voice.receipt_enabled", "recall.auto", "voice.bargein_note",
              "voice.quiet_window", "voice.turn_classify", "trust.remember",
              "voice.silence_gate"},
          f"{len(VOICE_FLAG_DEFAULTS)} flag")
    check("default semua ON",
          all(bool(get_flags().get(k)) for k in VOICE_FLAG_DEFAULTS))

    _orig_path = _config._FLAGS_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            _config._FLAGS_PATH = Path(td) / "f.json"
            check("berkas hilang -> default",
                  all(_config.get_flags().get(k) for k in VOICE_FLAG_DEFAULTS))
            _config._FLAGS_PATH.write_text(
                json.dumps({"voice.receipt_enabled": False}), encoding="utf-8")
            check("mati = perilaku lama",
                  _config.get_flags().get("voice.receipt_enabled") is False)
            _config._FLAGS_PATH.write_text(
                json.dumps({"voice.receipt_enabled": True}), encoding="utf-8")
            check("hidup = perilaku baru",
                  _config.get_flags().get("voice.receipt_enabled") is True)
            _config._FLAGS_PATH.write_text("{rusak", encoding="utf-8")
            check("berkas rusak -> default",
                  _config.get_flags().get("voice.receipt_enabled") is True)
    finally:
        _config._FLAGS_PATH = _orig_path

# ── 2. core.receipt — fungsi NYATA, bukan salinan ────────────────────────────
try:
    from core.receipt import (LANDED_TAG, RECEIPT_TAG, LandedRing,
                              compose_receipt, landed_line)
except Exception as e:                                   # noqa: BLE001
    print(f"  import core.receipt: {type(e).__name__}: {e} => FAIL")
    errs.append("import core.receipt")
else:
    r = compose_receipt("kirim email ke rafael@example.com subjek laporan")
    check("receipt bertagar", r.startswith(RECEIPT_TAG), r[:60])
    check("receipt menyebut pekerjaan", "rafael@example.com" in r, r[:90])
    check("receipt satu kalimat", r.count(".") <= 2 and "\n" not in r)
    check("tanpa detail -> nama tool dibuang",
          "send_message:" not in compose_receipt("send_message: kirim", "kirim"))
    check("detail kosong -> judul dipakai",
          "pekerjaan" in compose_receipt("", "").lower())
    check("receipt tanpa tag utk jalur otak",
          not compose_receipt("kirim", "kirim", tag=False).startswith("["))

    ring = LandedRing(cap=3)
    check("entri pertama masuk",
          ring.add("job1", landed_line("done", "sudah selesai")) is True)
    check("duplikat ditolak", ring.add("job1", "sudah selesai") is False)
    check("entri kedua berbeda diterima",
          ring.add("job2", landed_line("failed", "GAGAL — smtp menolak")))
    ring.add("job3", landed_line("done", "dokumen jadi"))
    ring.add("job4", landed_line("done", "entri keempat"))
    check("cincin dibatasi", len(ring) == 3, f"len={len(ring)}")
    blk = ring.block()
    check("blok bertagar", blk.startswith(LANDED_TAG[:20]), LANDED_TAG[:24])
    check("blok memuat kegagalan", "smtp" in blk)
    check("ring kosong -> blok kosong", LandedRing().block() == "")
    check("baris kosong ditolak", LandedRing().add("j9", "   ") is False)

# ── 3. main.py — struktur titik pikat ────────────────────────────────────────
check("main: compose_receipt dipakai", "compose_receipt(" in MAIN_SRC)
check("main: respons silent utk tool latar",
      MAIN_SRC.count('"silent": True') >= 2,
      f"silent x{MAIN_SRC.count(chr(34) + 'silent' + chr(34) + ': True')}")
check("main: cincin landed dipakai", "self._landed" in MAIN_SRC)
check("main: blok landed disuntik", "self._landed.block()" in MAIN_SRC)
check("main: token long_tools diberikan", '"long_tools":' in MAIN_SRC)
check("main: kalimat jembatan ada", "jembatan" in MAIN_SRC)
check("main: flag dibaca lewat def _flag(", "def _flag(" in MAIN_SRC)
# Urutan aman: tanda terima diantre, diucapkan hanya SETELAH respons tool
# terkirim. Menyelipkan giliran pengguna di tengah panggilan tool bisa
# membatalkan panggilan itu (barge-in) dan membuat sesi putus.
check("main: receipt diantre dulu", "self._receipt_pending" in MAIN_SRC)
check("main: receipt dilepas setelah respons",
      "def _flush_receipts" in MAIN_SRC
      and "await self._flush_receipts()" in MAIN_SRC)
_send_i = MAIN_SRC.find("await self.session.send_tool_response(")
_flush_i = MAIN_SRC.find("await self._flush_receipts()")
check("main: flush receipt sesudah send_tool_response",
      _send_i != -1 and _flush_i != -1 and _send_i < _flush_i)
check("main: receipt tidak diselipkan di tengah tool call",
      "_receipt_pending.append" in MAIN_SRC
      and "self.plugin_say(compose_receipt" not in MAIN_SRC)

# ── 4. prompt ────────────────────────────────────────────────────────────────
check("prompt: token long_tools", "{long_tools}" in PROMPT_SRC)
check("prompt: tag PEKERJAAN_DITERIMA", "PEKERJAAN_DITERIMA" in PROMPT_SRC)
# Definisi tag ada di prompt; frasa pemilik ucapan datang dari nilai token
# {long_tools} di main.py (disuntik saat render). Keduanya harus ada.
check("prompt: penerimaan milik aplikasi",
      "aplikasi sudah mengucapkan" in MAIN_SRC
      and "jangan mengucapkan penerimaan" in PROMPT_FLAT)

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
