"""
voice_journal.py — a diary, not a database.

WHY THIS IS NOT THE MEMORY SYSTEM
    JARVIS already remembers facts: preferences, names, the city you live in.
    Those are extracted, rewritten and pruned so they stay useful. A journal is
    the opposite of that — it is what you said, in your words, in the order you
    said it, and it must never be summarised away or merged with anything. So
    entries go to a dated markdown file and stay there: one file per day under
    memory/journal/, nothing rewritten, nothing pruned.

WHAT YOU CAN DO WITH IT
    Say something and it is filed with the time ("note that the plumber comes
    Thursday"). Ask for today and it is read back in order. Ask for the evening
    recap and the day's entries are condensed into a few lines — generated on
    demand, never stored over the originals.
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from core import gemini as _gemini

_DIR = Path(__file__).resolve().parent.parent / "memory" / "journal"

PLUGIN = {
    "name": "voice_journal",
    "description": (
        "A personal diary the user dictates to: entries are saved with the time "
        "in a dated file and can be read back, searched, or recapped at the end "
        "of the day. Use for: 'note that ...', 'journal: ...', 'dear diary', "
        "'what did I write today', 'read my journal from yesterday', 'search my "
        "journal for X', 'recap my day'. This is for the user's own narrative "
        "notes — for facts JARVIS should remember and use later, that is the "
        "memory system, not this."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'add' (default) file an entry, 'read' read a day back, "
                    "'list' show recent days, 'search' look for a word, "
                    "'recap' condense a day into a few lines."
                ),
            },
            "text": {
                "type": "STRING",
                "description": "For 'add': what to record. For 'search': the word to look for.",
            },
            "day": {
                "type": "STRING",
                "description": (
                    "For 'read'/'recap': 'today' (default), 'yesterday', or a "
                    "date like 2026-10-14."
                ),
            },
        },
        "required": [],
    },
}


def _log(player, msg: str) -> None:
    if player:
        try:
            player.write_log(msg)
        except Exception:
            pass


def _path_for(day: date) -> Path:
    return _DIR / f"{day.isoformat()}.md"


def _resolve_day(value: str) -> date | None:
    v = (value or "today").strip().lower()
    if v in ("today", "hari ini", ""):
        return date.today()
    if v in ("yesterday", "kemarin"):
        return date.today() - timedelta(days=1)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", v)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _entries(day: date) -> list[str]:
    p = _path_for(day)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    return [l[2:].strip() for l in lines if l.startswith("- ")]


def _add(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "What should I write down?"
    today = date.today()
    p = _path_for(today)
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        new = not p.exists()
        with p.open("a", encoding="utf-8") as f:
            if new:
                f.write(f"# Journal — {today.isoformat()}\n\n")
            f.write(f"- {datetime.now():%H:%M} {text}\n")
    except Exception as e:
        return f"I couldn't write that down: {e}"
    n = len(_entries(today))
    return f"Noted ({n} entr{'y' if n == 1 else 'ies'} today)."


def _read(day: date) -> str:
    entries = _entries(day)
    if not entries:
        return f"Nothing in the journal for {day.isoformat()}."
    return f"Journal for {day.isoformat()}:\n" + "\n".join(f"• {e}" for e in entries)


def _list() -> str:
    try:
        files = sorted(_DIR.glob("*.md"), reverse=True)[:10]
    except Exception:
        files = []
    if not files:
        return "The journal is empty so far."
    out = []
    for f in files:
        try:
            count = sum(1 for l in f.read_text(encoding="utf-8").splitlines()
                        if l.startswith("- "))
        except Exception:
            count = 0
        out.append(f"{f.stem}: {count} entr{'y' if count == 1 else 'ies'}")
    return "Recent journal days:\n" + "\n".join(out)


def _search(word: str) -> str:
    word = (word or "").strip()
    if not word:
        return "What word should I look for?"
    hits = []
    try:
        for f in sorted(_DIR.glob("*.md"), reverse=True):
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.startswith("- ") and word.lower() in line.lower():
                        hits.append(f"{f.stem}: {line[2:].strip()}")
            except Exception:
                continue
            if len(hits) >= 15:
                break
    except Exception:
        pass
    if not hits:
        return f"Nothing in the journal mentions '{word}'."
    return f"{len(hits)} match(es) for '{word}':\n" + "\n".join(f"• {h}" for h in hits[:15])


def _recap(day: date) -> str:
    entries = _entries(day)
    if not entries:
        return f"There is nothing recorded for {day.isoformat()} to recap."
    try:
        prompt = (
            "Here are the user's own journal entries for one day, in order.\n"
            "Write a short first-person recap of the day — 3 to 5 sentences, "
            "warm and plain, in the language of the entries. Do not invent "
            "anything that is not there.\n\n" + "\n".join(entries)
        )
        resp = _gemini.call(prompt, tier=_gemini.SMART, timeout_ms=45000)
        if resp is None:
            return _read(day)
        text = (getattr(resp, "text", "") or "").strip()
        return text or _read(day)
    except Exception:
        return _read(day)


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "add").strip().lower()
    try:
        if action in ("add", "note", "write", "log"):
            return _add(parameters.get("text") or "")

        if action in ("list", "days"):
            return _list()

        if action == "search":
            return _search(parameters.get("text") or parameters.get("word") or "")

        if action in ("read", "show"):
            day = _resolve_day(parameters.get("day") or "today")
            if day is None:
                return "I couldn't read that date — try 'today', 'yesterday' or 2026-10-14."
            result = _read(day)
            if player:
                try:
                    player.show_content(f"📔 JOURNAL — {day.isoformat()}", result[:4000])
                except Exception:
                    pass
            return result

        if action in ("recap", "summary", "summarise", "summarize"):
            day = _resolve_day(parameters.get("day") or "today")
            if day is None:
                return "I couldn't read that date — try 'today', 'yesterday' or 2026-10-14."
            result = _recap(day)
            _log(player, f"JARVIS: Journal recap for {day.isoformat()}.")
            return result

        return f"Unknown journal action '{action}'."
    except Exception as e:
        return f"Sir, the journal failed: {e}"
