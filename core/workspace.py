"""
core/workspace.py — where the last piece of work actually lives.

WHY THIS EXISTS
    Every run of dev_agent planned a project name and created a folder for it,
    so "revisi halamannya" produced a NEW folder next to the old one instead of
    editing the files already there. The assistant was not forgetting on
    purpose — nothing in the process remembered the path between turns.

    This is that memory: one small JSON file, written when a project is built,
    read when the next instruction arrives. It is deliberately not part of
    long_term.json — this is operational state, not something the user said,
    and it must not be injected as a "fact about the user".
"""
from __future__ import annotations

import json
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PATH = _ROOT / "memory" / "workspace.json"


def remember(directory, files=None, note: str = "") -> None:
    """Record the folder a project was just written to."""
    try:
        d = Path(str(directory)).expanduser()
        names = []
        if d.is_dir():
            names = sorted(p.name for p in d.iterdir()
                           if p.is_file())[:40]
        data = {
            "dir": str(d),
            "files": list(files or names)[:40],
            "note": str(note or "")[:160],
            "ts": time.strftime("%Y-%m-%d %H:%M"),
        }
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    except Exception as e:                       # noqa: BLE001
        print(f"[Workspace] tidak bisa menyimpan: {e}")


def last() -> dict:
    """The remembered workspace, or {} when there is none (or it vanished)."""
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        d = Path(str(data.get("dir") or ""))
        if not d.is_dir():
            return {}
        return data
    except Exception:
        return {}


def describe() -> str:
    """One line for the system prompt, or "" when nothing is remembered."""
    data = last()
    if not data:
        return ""
    files = data.get("files") or []
    listing = ", ".join(files[:8]) + ("…" if len(files) > 8 else "")
    return (f"Folder kerja terakhir: {data['dir']}"
            + (f" (isi: {listing})" if listing else ""))


def target_dir(explicit=None) -> Path:
    """Where a NEW file should be written.

    One rule for every writer — Excel, documents, plain files:
      1. the folder the user named, if any;
      2. the folder of the last project;
      3. the Desktop.
    Sharing this keeps "buat file di folder kerja saya" honest no matter
    which tool ends up doing the writing.
    """
    if explicit:
        p = Path(str(explicit)).expanduser()
        return p
    data = last()
    if data and Path(str(data.get("dir") or "")).is_dir():
        return Path(str(data["dir"]))
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


REVISE_HINTS = ("revisi", "perbaiki", "perbarui", "update", "ubah", "ganti",
                "tambahkan", "tambah", "edit", "lanjutkan", "sempurnakan",
                "kurangi", "hapus bagian", "revisi", "perbaikan")


def looks_like_revision(text: str) -> bool:
    """True when the instruction is about changing existing work."""
    low = (text or "").lower()
    return any(h in low for h in REVISE_HINTS)
