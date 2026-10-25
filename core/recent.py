"""
recent.py — the files JARVIS has actually worked with.

The bottom card in the HUD says RECENT PROJECTS, and a card that shows a
hardcoded list would be decoration. This is the smallest honest version: a
JSON list in memory/recent_files.json, written whenever a file is picked in
the HUD or produced/moved by the file tools, read back for the card.

Bounded on purpose — 12 entries, newest first, same path only once — so the
file cannot grow for the life of the install.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

_MAX = 12
_BASE = Path(__file__).resolve().parent.parent
_FILE = _BASE / "memory" / "recent_files.json"


def _read() -> list[dict]:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict) and d.get("path")]
    except Exception:
        return []


def add(path: str) -> None:
    """Record one path. Missing files are dropped at read time, not here —
    a file that exists now may be gone tomorrow, and the card should show
    what is still there."""
    path = str(path or "").strip()
    if not path:
        return
    items = [d for d in _read() if d.get("path") != path]
    items.insert(0, {"path": path, "ts": time.time()})
    items = items[:_MAX]
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")
    except Exception:
        pass


def items(limit: int = 4) -> list[dict]:
    """Newest first, with a display name and an age string for the card."""
    out = []
    now = time.time()
    for d in _read():
        p = Path(d["path"])
        age = max(0, int(now - float(d.get("ts") or now)))
        if age < 60:
            when = "baru saja"
        elif age < 3600:
            when = f"{age // 60} mnt lalu"
        elif age < 86400:
            when = f"{age // 3600} jam lalu"
        else:
            when = f"{age // 86400} hari lalu"
        out.append({"path": d["path"], "name": p.name or str(p), "when": when,
                    "exists": p.exists()})
        if len(out) >= limit:
            break
    return out
