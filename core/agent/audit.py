"""
core/agent/audit.py — catatan tindakan yang tidak bisa dihapus diam-diam.

Setiap pemanggilan tool (dari otak, dari agent, atau dari plugin) ditulis ke
satu baris JSONL. Tujuannya bukan logging debug — konsol sudah punya itu —
tapi jejak yang bisa ditanyakan: "apa yang kamu lakukan 10 menit lalu?".

Format satu baris:
    {"ts": "...", "actor": "brain|agent|voice", "tool": "...",
     "args": {...}, "decision": "allow|confirm|deny",
     "ok": true, "ms": 42, "summary": "..."}

Berkas dibatasi ukurannya (rotasi sederhana): kalau melewati MAX_BYTES,
berkas lama diganti nama .1 dan yang baru dimulai. Tanpa batas, audit trail
akan tumbuh selamanya di mesin yang dipakai berbulan-bulan.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PATH = ROOT / "memory" / "audit.jsonl"
MAX_BYTES = 2 * 1024 * 1024       # 2 MB per berkas


def _rotate_if_needed() -> None:
    try:
        if PATH.exists() and PATH.stat().st_size > MAX_BYTES:
            backup = PATH.with_suffix(".jsonl.1")
            if backup.exists():
                backup.unlink()
            PATH.rename(backup)
    except Exception:
        pass


def _clip(value, limit: int = 300):
    """Argumen bisa berisi apa saja; simpan bentuk yang aman dibaca."""
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:12]:
            if any(s in str(k).lower() for s in ("key", "token", "secret",
                                                 "password", "api")):
                out[k] = "***"
            else:
                out[k] = _clip(v, 120)
        return out
    if isinstance(value, (list, tuple)):
        return [_clip(v, 120) for v in list(value)[:8]]
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def record(tool: str, args=None, *, actor: str = "brain",
           decision: str = "allow", ok: bool = True, ms: int = 0,
           summary: str = "") -> None:
    """Append one action. Never raises — an audit failure must not stop work."""
    try:
        _rotate_if_needed()
        line = json.dumps({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "actor": actor,
            "tool": str(tool),
            "args": _clip(args or {}),
            "decision": decision,
            "ok": bool(ok),
            "ms": int(ms),
            "summary": _clip(summary, 240),
        }, ensure_ascii=False)
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:                       # noqa: BLE001
        print(f"[Audit] gagal menulis: {e}")


def tail(n: int = 20) -> list[dict]:
    """Baca n baris terakhir — dipakai diagnosa & pertanyaan 'apa yang tadi'."""
    try:
        lines = PATH.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:
        return []


def recent_tools(limit: int = 10) -> list[str]:
    """Nama tool terakhir, untuk kalimat seperti 'saya baru menjalankan X'."""
    return [r.get("tool", "") for r in tail(limit)]
