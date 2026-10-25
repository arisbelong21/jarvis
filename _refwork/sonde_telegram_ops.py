# -*- coding: utf-8 -*-
"""Sonde remote — buka app, klik elemen, dan burst layar dari Telegram.

MERAH terhadap kode SEKARANG: _telegram_ops.EXTRA_OPS belum punya op
open_app/click/watch, belum ada runner-nya, dan belum ada album_payload
untuk sendMediaGroup. Panel /help dan menu Telegram dibangun dari tabel itu,
jadi op yang tidak ada di tabel tidak ada di ponsel.

Yang diuji: kode NYATA _telegram_ops (impor murah — impor berat sengaja
ditunda di dalam fungsi). Yang distub HANYA dependensi luar: _grab dan
_compress (perangkat layar) untuk menguji orkestrasi _watch; fungsi yang
diuji tidak diganti.
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


try:
    from plugins import _telegram_ops as ops
except Exception as e:                                   # noqa: BLE001
    print(f"  impor _telegram_ops: {type(e).__name__}: {e} => FAIL")
    errs.append("impor")
    raise SystemExit(1)

# ── 1. tabel op: tiga kemampuan baru ────────────────────────────────────────
print("== tabel op ==")
by_action = {o.get("action"): o for o in ops.EXTRA_OPS}
for action, cmd in (("open_app", "open"), ("click", "click"), ("watch", "watch")):
    op = by_action.get(action)
    check(f"op '{action}' ada", op is not None)
    if op:
        check(f"op '{action}': cmd = {cmd}", op.get("cmd") == cmd, op.get("cmd"))
        check(f"op '{action}': punya deskripsi", bool((op.get("desc") or "").strip()))
        check(f"op '{action}': where valid", op.get("where") in ("desk", "remote", "both"),
              op.get("where"))
        check(f"op '{action}': punya hint arg", bool(op.get("arg")))

check("op click: satu tekan (tanpa konfirmasi dua-tekan)",
      by_action.get("click", {}).get("confirm") is not True)
check("op watch: di balik sakelar allow_screenshot",
      by_action.get("watch", {}).get("gate") == "allow_screenshot")
check("op open_app: tanpa gerbang tambahan",
      by_action.get("open_app", {}).get("gate") is None)

# ── 1b. tag [FILE] dijelaskan di prompt (model tak boleh menebak) ───────────
_prompt = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
check("prompt: tag [FILE] dijelaskan", "[FILE]" in _prompt)
check("prompt: melarang bertanya 'file mana'", "never ask which file" in _prompt)

# ── 2. runner terdaftar ─────────────────────────────────────────────────────
print("== runner ==")
for action in ("open_app", "click", "watch"):
    check(f"runner '{action}' terdaftar", action in ops._RUNNERS)

# ── 3. preview click menyebut apa yang akan diklik ──────────────────────────
print("== preview ==")
try:
    p1 = ops.preview("click", "tombol Coba Lagi")
    check("preview click menyebut argumen", "tombol Coba Lagi" in p1, p1[:60])
except TypeError:
    check("preview menerima argumen kedua", False, "preview(action) saja")
except Exception as e:                                   # noqa: BLE001
    check("preview click tidak melempar", False, f"{type(e).__name__}: {e}")

# ── 4. _watch: orkestrasi (dependensi layar distub) ─────────────────────────
print("== _watch ==")
_orig_grab, _orig_comp = ops._grab, ops._compress
try:
    calls = {"n": 0}

    def _fake_grab(arg=""):
        calls["n"] += 1
        return b"PNG", 1920, 1080, 1, 1

    def _fake_comp(png, max_w=None, max_h=None):
        return b"JPEG", "image/jpeg", "jpg"

    ops._grab, ops._compress = _fake_grab, _fake_comp
    job = ops.produce("watch", "3", {"allow_screenshot": True})
    check("watch menghasilkan album", job.get("kind") == "album", job.get("kind"))
    check("watch: 3 detik = 3 frame", len(job.get("frames") or []) == 3,
          len(job.get("frames") or []))
    check("watch: grab dipanggil sekali per frame", calls["n"] == 3, calls["n"])
    check("watch: caption ada", bool(job.get("caption")))
    job2 = ops.produce("watch", "99", {"allow_screenshot": True})
    check("watch: dibatasi maksimum 10 frame", len(job2.get("frames") or []) == 10,
          len(job2.get("frames") or []))
finally:
    ops._grab, ops._compress = _orig_grab, _orig_comp

# ── 5. album_payload untuk sendMediaGroup ───────────────────────────────────
print("== album_payload ==")
job = {"kind": "album", "chat": 42, "caption": "layar", "name": "frame",
       "frames": [(b"a", "image/jpeg"), (b"b", "image/jpeg"), (b"c", "image/jpeg")]}
try:
    data, files = ops.album_payload(job)
    import json as _json
    media = _json.loads(data["media"])
    check("album: chat_id ikut", data.get("chat_id") == 42, data.get("chat_id"))
    check("album: 3 item media", len(media) == 3, len(media))
    check("album: semua attach://", all(m["media"].startswith("attach://") for m in media))
    check("album: caption hanya di item pertama",
          media[0].get("caption") == "layar" and "caption" not in media[1])
    check("album: 3 berkas terkirim", len(files) == 3, list(files))
except Exception as e:                                   # noqa: BLE001
    check("album_payload bisa dipanggil", False, f"{type(e).__name__}: {e}")

# ── 6. suara: balasan yang benar-benar bisa didengar ────────────────────────
print("== suara ==")
_src = (ROOT / "plugins" / "_telegram_ops.py").read_text(encoding="utf-8")
check("tidak lagi mengimpor modul yang tak ada (providers.audio)",
      "from providers import audio" not in _src)
check("ada suara bawaan sistem (_system_voice)", hasattr(ops, "_system_voice"))
try:
    job = ops.synthesize("Halo, ini uji suara dari JARVIS.")
    check("synthesize menghasilkan audio", job.get("kind") in ("voice", "audio")
          and len(job.get("data") or b"") > 5000,
          f"{job.get('kind')} {len(job.get('data') or b'')} B")
    check("synthesize melaporkan durasi", int(job.get("seconds") or 0) >= 1,
          job.get("seconds"))
except Exception as e:                                   # noqa: BLE001
    check("synthesize menghasilkan audio", False, f"{type(e).__name__}: {e}")

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
