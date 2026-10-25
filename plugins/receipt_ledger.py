"""
receipt_ledger.py — photograph a receipt, get a ledger.

THE FLOW IS TWO STEPS ON PURPOSE
    Reading a receipt is a guess, even for a good model: blur, folds, a
    handwritten total, a discount line. So the reading is never written to the
    ledger directly. It is shown back — store, date, total — and only "save it"
    files it. That one confirmation is what keeps a misread total from quietly
    becoming part of the month's spending, and it costs one sentence.

WHERE THE MONEY GOES
    memory/expenses.csv, one row per receipt, appended. Deliberately a CSV:
    it opens in anything, it is greppable, and no part of this feature depends
    on a spreadsheet library being installed. "undo" removes the last row, and
    the row is also registered in the app's undo stack, so the ordinary undo
    command works on it too.

A TOTAL IS NEVER INVENTED
    If the model cannot find a total, the plugin says so and offers the
    dictated path instead ("just tell me the amount"). A blank or guessed
    number in a ledger is worse than a missing one.
"""

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path

from core import gemini as _gemini

_CSV = Path(__file__).resolve().parent.parent / "memory" / "expenses.csv"
_HEADER = ["date", "store", "total", "currency", "note", "source"]

PLUGIN = {
    "name": "receipt_ledger",
    "description": (
        "Turns a receipt into a ledger row: reads the store, date and total "
        "from a photo, a screenshot or a dictated sentence, shows the reading "
        "back for confirmation, then appends it to memory/expenses.csv. Use "
        "for: 'log this receipt', 'add this expense', 'record that I spent X at "
        "Y', 'how much have I spent this month', 'show my last expenses', 'undo "
        "the last expense'. Reading is only saved after the user confirms — "
        "never write a receipt into the ledger on the reading alone."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'add' (default) read a receipt — from a screenshot, an "
                    "image file, or dictated text; 'save' write the reading that "
                    "is waiting; 'cancel' throw it away; 'last' show recent "
                    "entries; 'month' total this month; 'undo' remove the last "
                    "entry."
                ),
            },
            "text": {
                "type": "STRING",
                "description": (
                    "For 'add': the expense in the user's own words, e.g. "
                    "'50 ribu di warung kopi tadi pagi'."
                ),
            },
            "file_path": {
                "type": "STRING",
                "description": "For 'add': path to a receipt image, if the user gave one.",
            },
        },
        "required": [],
    },
}

_lock_state: dict = {"pending": None}


def _log(player, msg: str) -> None:
    if player:
        try:
            player.write_log(msg)
        except Exception:
            pass


# ── reading a receipt ────────────────────────────────────────────────────────

def _screenshot_bytes() -> bytes | None:
    try:
        import io
        import mss
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.rgb)
        if img.width > 1400:
            img = img.resize((1400, int(img.height * 1400 / img.width)))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None


_PROMPT = (
    "Read this receipt or expense and reply with ONLY a JSON object:\n"
    '{"store": "...", "date": "YYYY-MM-DD or empty", "total": 0.0, '
    '"currency": "Rp or USD or EUR or ...", "note": "one short line"}\n'
    "Rules: total is the FINAL amount paid, a plain number with a dot as the "
    "decimal separator and NO thousands separators (1234567.00). If the total "
    "is not clearly visible, use null. Never guess a total. No commentary, no "
    "code fences."
)


def _extract_from_image(data: bytes) -> dict | None:
    try:
        from google import genai
        from google.genai import types
        key = _gemini.api_key()
        if not key:
            return None
        client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
        part = types.Part.from_bytes(data=data, mime_type="image/png")
        for model in ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-flash-latest"):
            try:
                resp = client.models.generate_content(model=model,
                                                      contents=[_PROMPT, part])
                return _parse_json(getattr(resp, "text", "") or "")
            except Exception:
                continue
    except Exception:
        return None
    return None


def _extract_from_text(text: str) -> dict | None:
    try:
        resp = _gemini.call(_PROMPT + "\n\nThe expense, dictated by the user:\n" + text,
                            tier=_gemini.FAST, timeout_ms=25000)
        if resp is None:
            return None
        return _parse_json(getattr(resp, "text", "") or "")
    except Exception:
        return None


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    total = data.get("total")
    if total is not None:
        try:
            data["total"] = float(str(total).replace(",", "."))
        except (TypeError, ValueError):
            data["total"] = None
    return data


# ── ledger ───────────────────────────────────────────────────────────────────

def _rows() -> list[list[str]]:
    try:
        with _CSV.open("r", encoding="utf-8", newline="") as f:
            return [r for r in csv.reader(f) if r and r[0] != _HEADER[0]]
    except Exception:
        return []


def _append(row: list[str]) -> None:
    _CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not _CSV.exists()
    with _CSV.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(_HEADER)
        w.writerow(row)


def _undo_last() -> str:
    rows = _rows()
    if not rows:
        return "The ledger is already empty."
    rows.pop()
    _CSV.parent.mkdir(parents=True, exist_ok=True)
    with _CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        w.writerows(rows)
    return "Removed the last entry from the ledger."


def _fmt(total, currency: str) -> str:
    try:
        if (currency or "").upper() in ("RP", "IDR", "RUPIAH"):
            return "Rp" + f"{float(total):,.0f}".replace(",", ".")
        return f"{currency or ''} {float(total):,.2f}".strip()
    except Exception:
        return str(total)


def _describe(data: dict) -> str:
    store = (data.get("store") or "").strip() or "unknown place"
    cur = (data.get("currency") or "Rp").strip()
    day = (data.get("date") or "").strip() or date.today().isoformat()
    total = data.get("total")
    note = (data.get("note") or "").strip()
    total_txt = _fmt(total, cur) if total is not None else "no total found"
    return f"{store} — {total_txt} — {day}" + (f" ({note})" if note else "")


# ── entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "add").strip().lower()
    try:
        if action in ("save", "confirm", "simpan"):
            pending = _lock_state.get("pending")
            if not pending:
                return "There's nothing waiting to be saved."
            data = pending
            total = data.get("total")
            if total is None:
                return ("I still don't have a total for that one — tell me the "
                        "amount and I'll record it.")
            row = [data.get("date") or date.today().isoformat(),
                   (data.get("store") or "").strip(),
                   f"{float(total):.2f}",
                   (data.get("currency") or "Rp").strip(),
                   (data.get("note") or "").strip(),
                   data.get("source") or "receipt"]
            try:
                _append(row)
            except Exception as e:
                return f"I couldn't write to the ledger: {e}"
            _lock_state["pending"] = None
            try:
                from core import undo as undo_stack
                undo_stack.push_undo("receipt entry", lambda: _undo_last())
            except Exception:
                pass
            _log(player, f"JARVIS: Ledger +{_fmt(total, row[3])} ({row[1] or 'unknown'}).")
            return (f"Saved: {row[1] or 'unknown place'}, {_fmt(total, row[3])} "
                    f"on {row[0]}. Say 'undo' if that's wrong.")

        if action in ("cancel", "discard"):
            _lock_state["pending"] = None
            return "Discarded — nothing was written to the ledger."

        if action in ("last", "recent"):
            rows = _rows()[-5:]
            if not rows:
                return "The ledger is empty."
            lines = [f"{r[0]} — {r[1] or 'unknown'} — {_fmt(r[2], r[3])}" for r in rows]
            return "Last entries:\n" + "\n".join(lines)

        if action in ("month", "total", "summary"):
            rows = _rows()
            month = datetime.now().strftime("%Y-%m")
            picked = [r for r in rows if (r[0] or "").startswith(month)]
            if not picked:
                return f"No expenses recorded in {month}."
            sums: dict[str, float] = {}
            for r in picked:
                try:
                    sums[r[3] or "Rp"] = sums.get(r[3] or "Rp", 0.0) + float(r[2])
                except Exception:
                    continue
            parts = [_fmt(v, k) for k, v in sums.items()]
            return (f"{len(picked)} entries in {month}, totalling "
                    + " + ".join(parts) + ".")

        if action == "undo":
            return _undo_last()

        # -------- ADD --------
        text = (parameters.get("text") or "").strip()
        file_path = (parameters.get("file_path") or "").strip()

        data = None
        source = "receipt"
        if file_path:
            p = Path(file_path)
            if not p.is_absolute():
                cand = Path.home() / "Desktop" / file_path
                p = cand if cand.exists() else p
            try:
                data = _extract_from_image(p.read_bytes())
                source = p.name
            except Exception as e:
                return f"I couldn't read that image: {e}"
        elif text:
            data = _extract_from_text(text)
            source = "dictated"
        else:
            blob = _screenshot_bytes()
            if not blob:
                return ("I couldn't take a screenshot, sir — pass me an image "
                        "path, or just tell me the amount.")
            data = _extract_from_image(blob)
            source = "screenshot"

        if not data:
            return ("I couldn't read that one. Tell me the amount (and the place) "
                    "and I'll record it.")

        data["source"] = source
        _lock_state["pending"] = data
        described = _describe(data)
        if data.get("total") is None:
            return (f"I read: {described}. I couldn't find a total — tell me the "
                    f"amount and I'll save it.")
        return (f"I read: {described}. Say 'save it' and I'll record it, or tell "
                f"me what to change.")
    except Exception as e:
        return f"Sir, the receipt ledger failed: {e}"
