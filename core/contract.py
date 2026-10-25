"""
core/contract.py — verifikasi hasil tool berbasis KONTRAK, bukan cocok-teks.

Masalah yang diselesaikan
    Sebelumnya core/decision_loop.verify() menilai "berhasil/gagal" dengan
    mengendus awalan kalimat hasil tool ("Opened ", "Clicked", "exit_code: 0",
    dst). Begitu sebuah tool mengubah susunan katanya — atau UI/aplikasi
    berubah — verifikasi bisa diam-diam meleset walau aksinya sukses.

Kontrak
    Tool yang sudah dimigrasi menempelkan satu penanda mesin (sentinel) di
    AKHIR teks hasilnya. Sentinel itu memuat fakta terstruktur:

        {"ok": true|false|null, "tool": "...", "ev": {...}, "note": "..."}

    verify() lalu membaca field `ok` sebagai kebenaran, bukan menebak dari
    kata. Teks yang dilihat manusia/model TETAP sama persis; sentinel selalu
    di-strip sebelum hasil dipakai ke mana pun.

Jaminan aman (kenapa tidak mengganggu sistem lain)
    • Sentinel ditempel di AKHIR string → semua cek `startswith(...)` dan
      substring lama ("Opened ", "exit_code: N") tetap utuh.
    • Payload di-base64url → tak mungkin bentrok dengan tanda baca teks.
    • backward-compatible: tool yang belum dimigrasi tidak menempel sentinel,
      parse() mengembalikan None, dan verify() jatuh ke heuristik lama.
    • strip() idempoten dan tak pernah melempar exception.
"""
from __future__ import annotations

import base64
import json
import re

_TAG = "JARVIS-CONTRACT-v1"
# \n? di depan ikut ter-strip agar tidak meninggalkan baris kosong.
_RE = re.compile(r"\n?\[\[" + re.escape(_TAG) + r":([A-Za-z0-9_\-]+=*)\]\]")


def _encode(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(b64: str) -> dict | None:
    try:
        pad = "=" * (-len(b64) % 4)
        raw = base64.urlsafe_b64decode(b64 + pad)
        obj = json.loads(raw.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:                                    # noqa: BLE001
        return None


def parse(text):
    """(payload|None, clean_text). payload None = tool belum pakai kontrak.

    clean_text adalah teks asli dengan sentinel dibuang, aman ditampilkan ke
    pengguna/model. Tak pernah melempar exception.
    """
    if text is None:
        return None, ""
    s = text if isinstance(text, str) else str(text)
    m = _RE.search(s)
    if not m:
        return None, s
    payload = _decode(m.group(1))
    clean = _RE.sub("", s)
    if payload is None:
        # Sentinel ada tapi rusak → buang saja, anggap tanpa kontrak.
        return None, clean
    return payload, clean


def strip(text):
    """Buang sentinel (bila ada) dari teks. Idempoten, tak pernah melempar."""
    _payload, clean = parse(text)
    return clean


def _attach(text, ok, tool=None, note=None, **ev) -> str:
    base = "" if text is None else (text if isinstance(text, str) else str(text))
    payload: dict = {"ok": ok}
    if tool:
        payload["tool"] = str(tool)
    if note:
        payload["note"] = str(note)
    if ev:
        # Buang nilai None agar payload tetap ramping.
        payload["ev"] = {k: v for k, v in ev.items() if v is not None}
    return f"{base}\n[[{_TAG}:{_encode(payload)}]]"


def ok(text, tool: str | None = None, note: str | None = None, **ev) -> str:
    """Tandai hasil sebagai TERVERIFIKASI berhasil, dengan bukti (ev)."""
    return _attach(text, True, tool, note, **ev)


def fail(text, tool: str | None = None, note: str | None = None, **ev) -> str:
    """Tandai hasil sebagai GAGAL (terbukti tidak terjadi)."""
    return _attach(text, False, tool, note, **ev)


def unknown(text, tool: str | None = None, note: str | None = None, **ev) -> str:
    """Tandai hasil sebagai TIDAK TERBUKTI (mungkin berhasil, tanpa bukti).

    verify() akan menambahkan UNVERIFIED_NOTE kecuali `note` diisi sendiri.
    """
    return _attach(text, None, tool, note, **ev)
