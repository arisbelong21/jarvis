"""
calendar_assistant.py — "what's on today", and a nudge before it starts.

WHY AN ICS LINK AND NOT GOOGLE OAUTH
    Google Calendar has a "secret address in iCal format": one URL, no OAuth
    client, no consent screen, no token that expires in an hour, no browser
    round trip in the middle of a voice command. It is read-only, which is
    exactly the access this plugin needs. The same URL format is served by
    Outlook, Nextcloud and anything else that speaks iCal, so one field covers
    every calendar the user is likely to have. Google's full API would add an
    OAuth dance and a credential file for no capability this plugin uses.

THE HONEST LIMIT — READ THIS BEFORE TRUSTING THE ALERTS
    Alerts only fire while JARVIS is running on this computer. If the PC is
    off, nothing reminds anybody — a local assistant cannot wake a machine it
    is not running on. That is why "remind me on my phone" belongs to the
    cloud side of this project (cloud_agent + Telegram), not here. The plugin
    says this once, in the settings label, instead of quietly missing meetings.

WHAT IT PARSES
    VEVENT blocks with DTSTART/DTEND/SUMMARY/LOCATION/DESCRIPTION, including
    UTC (…Z), TZID=… (via zoneinfo) and all-day (VALUE=DATE) forms, plus
    line unfolding. Recurrence is expanded for the common shapes — DAILY,
    WEEKLY (with BYDAY), MONTHLY and YEARLY, honouring INTERVAL, COUNT and
    UNTIL. Anything more exotic (BYSETPOS and friends) falls back to showing
    the first occurrence only, rather than inventing dates that are not there.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_CACHE = Path(__file__).resolve().parent.parent / "memory" / "calendar_cache.ics"
_STATE = Path(__file__).resolve().parent.parent / "memory" / "calendar_state.json"
_NS = "calendar"

PLUGIN = {
    "name": "calendar_assistant",
    "description": (
        "Reads the user's calendar (Google/Outlook/Nextcloud iCal link) and "
        "answers what is coming up, plus a spoken nudge shortly before each "
        "event while JARVIS is running. Use for: 'what's on today', 'what's my "
        "schedule', 'any meetings tomorrow', 'what's next on my calendar', "
        "'refresh my calendar'. Indonesian: 'cek jadwal', 'agenda hari ini', "
        "'acara besok'. It is read-only and it cannot remind anybody "
        "while this computer is off."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'today' (default) what is left today, 'tomorrow', 'week' "
                    "the next 7 days, 'next' the very next event, 'refresh' "
                    "re-download the calendar."
                ),
            },
            "days": {
                "type": "INTEGER",
                "description": "For 'week': how many days ahead (default 7).",
            },
        },
        "required": [],
    },
}

PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "📅  CALENDAR",
    "fields": [
        {"key": "ics_url", "label": "Calendar iCal link (Google: Settings → your "
         "calendar → 'Secret address in iCal format')", "type": "password",
         "placeholder": "https://calendar.google.com/calendar/ical/…/basic.ics"},
        {"key": "lead_minutes", "label": "Nudge me this many minutes before an event",
         "type": "text", "default": "10", "placeholder": "10"},
        {"key": "check_interval_minutes", "label": "How often to re-check the calendar",
         "type": "text", "default": "5", "placeholder": "5"},
        {"key": "alerts_enabled", "label": "Speak the nudges out loud", "type": "toggle",
         "default": True},
        {"key": "local_only_note",
         "label": "Nudges only work while JARVIS is running — nothing can remind "
                  "you while this computer is off.", "type": "label", "default": ""},
    ],
}


def _setting(key, default=None):
    try:
        from memory.config_manager import get_plugin_setting
        val = get_plugin_setting(_NS, key, default)
        return default if val in (None, "") else val
    except Exception:
        return default


def _as_int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(float(value))))
    except (TypeError, ValueError):
        return default


# ── iCal parsing ─────────────────────────────────────────────────────────────

def _unfold(text: str) -> list[str]:
    """RFC 5545 line folding: a line starting with a space continues the previous."""
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _parse_dt(value: str, tzid: str = ""):
    """→ (datetime, is_all_day). ALWAYS naive LOCAL time.

    Everything in this module — comparisons, formatting, the nudge loop — works
    in one clock, so a UTC (…Z) or TZID-stamped stamp is converted to local and
    then made naive. Mixing aware and naive datetimes is not a style question:
    Python raises on the comparison, and the failure lands in the middle of an
    alert loop."""
    v = (value or "").strip()
    if not v:
        return None, False
    if re.fullmatch(r"\d{8}", v):                       # all-day
        try:
            return datetime(int(v[:4]), int(v[4:6]), int(v[6:8])), True
        except ValueError:
            return None, True
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)", v)
    if not m:
        return None, False
    y, mo, d, h, mi, s, z = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except ValueError:
        return None, False
    if z == "Z":
        return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None), False
    if tzid:
        try:
            from zoneinfo import ZoneInfo
            return dt.replace(tzinfo=ZoneInfo(tzid)).astimezone().replace(tzinfo=None), False
        except Exception:
            return dt, False
    return dt, False


_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _expand_rrule(start: datetime, rrule: str, until: datetime) -> list[datetime]:
    """The common recurrences, expanded between start and `until`. Unknown
    parts are ignored rather than guessed — the first occurrence is always
    included, so an unsupported rule degrades to 'shows once'."""
    parts = {}
    for chunk in (rrule or "").split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            parts[k.strip().upper()] = v.strip()
    freq = parts.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return [start]
    try:
        interval = max(1, int(parts.get("INTERVAL", "1")))
    except ValueError:
        interval = 1
    try:
        count = int(parts["COUNT"]) if "COUNT" in parts else None
    except ValueError:
        count = None
    hard_end = until
    if "UNTIL" in parts:
        u, _ = _parse_dt(parts["UNTIL"])
        if u:
            hard_end = min(hard_end, u)
    byday = [d for d in (parts.get("BYDAY", "") or "").split(",") if d]
    bymonthday = []
    if "BYMONTHDAY" in parts:
        for d in parts["BYMONTHDAY"].split(","):
            try:
                bymonthday.append(int(d))
            except ValueError:
                continue

    out: list[datetime] = []
    cur = start
    guard = 0
    while cur <= hard_end and guard < 1000:
        guard += 1
        if len(out) >= 400:
            break
        out.append(cur)
        if count is not None and len(out) >= count:
            break
        if freq == "DAILY":
            cur = cur + timedelta(days=interval)
        elif freq == "WEEKLY":
            if byday:
                # next matching weekday in the same interval week
                week_start = cur - timedelta(days=cur.weekday())
                nxt = None
                for off in range(1, 8 * interval + 1):
                    cand = cur + timedelta(days=off)
                    if cand.weekday() in {_WEEKDAYS.get(d[-2:], -1) for d in byday}:
                        if (cand - week_start).days // 7 % interval == 0 or interval == 1:
                            nxt = cand
                            break
                cur = nxt or (cur + timedelta(weeks=interval))
            else:
                cur = cur + timedelta(weeks=interval)
        elif freq == "MONTHLY":
            y, m = cur.year, cur.month
            days = bymonthday or [cur.day]
            cands = []
            for dd in days:
                try:
                    cands.append(cur.replace(year=y, month=m, day=dd))
                except ValueError:
                    continue
            nxt_month = (m + interval - 1) % 12 + 1
            y2 = y + (m + interval - 1) // 12
            future = []
            for dd in days:
                try:
                    future.append(cur.replace(year=y2, month=nxt_month, day=dd))
                except ValueError:
                    continue
            after = [c for c in cands if c > cur]
            cur = min(after) if after else (min(future) if future else cur + timedelta(days=31 * interval))
        else:  # YEARLY
            try:
                cur = cur.replace(year=cur.year + interval)
            except ValueError:
                cur = cur + timedelta(days=365 * interval)
    return out or [start]


def _parse_ics(text: str, horizon_days: int) -> list[dict]:
    now = datetime.now()
    until = now + timedelta(days=max(1, horizon_days))
    events: list[dict] = []
    cur: dict | None = None
    for line in _unfold(text):
        if line.startswith("BEGIN:VEVENT"):
            cur = {}
            continue
        if line.startswith("END:VEVENT"):
            if cur is not None:
                start, all_day = cur.get("start", (None, False))
                if start is not None:
                    dur = cur.get("dur", timedelta(hours=1))
                    rrule = cur.get("rrule", "")
                    occs = _expand_rrule(start, rrule, until) if rrule else [start]
                    for occ in occs:
                        if now - timedelta(hours=12) <= occ <= until:
                            events.append({
                                "start": occ,
                                "end": occ + dur,
                                "all_day": all_day,
                                "summary": cur.get("summary") or "(no title)",
                                "location": cur.get("location") or "",
                                "recurring": bool(rrule),
                            })
            cur = None
            continue
        if cur is None:
            continue
        if ":" not in line:
            continue
        head, _, value = line.partition(":")
        key = head.split(";", 1)[0].upper()
        params = head.split(";")[1:] if ";" in head else []
        tzid = ""
        for prm in params:
            if prm.upper().startswith("TZID="):
                tzid = prm.split("=", 1)[1]
        if key == "DTSTART":
            cur["start"] = _parse_dt(value, tzid)
        elif key == "DTEND":
            end_dt, _ = _parse_dt(value, tzid)
            if end_dt and cur.get("start", (None, False))[0]:
                cur["dur"] = max(timedelta(minutes=1), end_dt - cur["start"][0])
        elif key == "SUMMARY":
            cur["summary"] = value.strip()
        elif key == "LOCATION":
            cur["location"] = value.strip()
        elif key == "RRULE":
            cur["rrule"] = value.strip()
    events.sort(key=lambda e: e["start"])
    return events


def _fetch(url: str) -> str:
    import requests
    r = requests.get(url, timeout=25, headers={"User-Agent": "JARVIS-calendar/1.0"})
    r.raise_for_status()
    return r.text


def _refresh(force: bool = False) -> tuple[list[dict], str]:
    """(events, error). Uses a 10-minute disk cache so a question never waits
    on the network when the answer was fetched a minute ago."""
    url = str(_setting("ics_url", "") or "")
    if not url:
        return [], "not-configured"
    fresh = False
    try:
        if _CACHE.exists() and not force:
            age = datetime.now().timestamp() - _CACHE.stat().st_mtime
            fresh = age < 600
    except Exception:
        fresh = False
    text = ""
    if fresh:
        try:
            text = _CACHE.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
    if not text:
        try:
            text = _fetch(url)
            try:
                _CACHE.parent.mkdir(parents=True, exist_ok=True)
                _CACHE.write_text(text, encoding="utf-8")
            except Exception:
                pass
        except Exception as e:
            # fall back to a stale cache rather than nothing at all
            try:
                text = _CACHE.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            if not text:
                return [], f"could not fetch the calendar ({type(e).__name__})"
    return _parse_ics(text, _as_int(_setting("horizon_days", 14), 14, 1, 60)), ""


# ── formatting ───────────────────────────────────────────────────────────────

def _fmt_event(e: dict) -> str:
    start = e["start"]
    when = "all day" if e["all_day"] else start.strftime("%H:%M")
    day = start.strftime("%a %d %b")
    loc = f" @ {e['location']}" if e["location"] else ""
    rep = " (repeats)" if e["recurring"] else ""
    return f"{day} {when} — {e['summary']}{loc}{rep}"


def _events_between(events: list[dict], start: datetime, end: datetime) -> list[dict]:
    return [e for e in events if start <= e["start"] < end]


# ── background nudge loop ────────────────────────────────────────────────────

import threading

_lock = threading.Lock()
_loop = {"thread": None, "stop": None}


def _load_state() -> dict:
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE.parent.mkdir(parents=True, exist_ok=True)
        _STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _log(player, msg: str) -> None:
    if player:
        try:
            player.write_log(msg)
        except Exception:
            pass


def _say(player, instruction: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(instruction)
        except Exception:
            pass


def _worker(player, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            interval = _as_int(_setting("check_interval_minutes", 5), 5, 1, 120) * 60
            if stop.wait(interval):
                break
            if not _setting("alerts_enabled", True):
                continue
            events, err = _refresh(force=True)
            if err:
                continue
            lead = _as_int(_setting("lead_minutes", 10), 10, 1, 240)
            now = datetime.now()
            state = _load_state()
            alerted = state.setdefault("alerted", {})
            for e in events:
                if e["all_day"]:
                    continue
                key = f"{e['start'].isoformat()}|{e['summary'][:40]}"
                mins = (e["start"] - now).total_seconds() / 60.0
                if 0 <= mins <= lead and key not in alerted:
                    alerted[key] = datetime.now().isoformat(timespec="seconds")
                    where = f" at {e['location']}" if e["location"] else ""
                    _say(player, f"Tell the user in one short sentence: their "
                                 f"'{e['summary']}' starts in about "
                                 f"{max(1, int(mins))} minute(s){where}.")
                    _log(player, f"JARVIS: Calendar nudge — {e['summary']} in "
                                 f"{max(1, int(mins))} min.")
            # keep the alert memory from growing forever
            state["alerted"] = dict(list(alerted.items())[-200:])
            _save_state(state)
    finally:
        with _lock:
            _loop["thread"] = None
            _loop["stop"] = None


def _ensure_loop(player) -> None:
    with _lock:
        if _loop["thread"] and _loop["thread"].is_alive():
            return
        stop = threading.Event()
        th = threading.Thread(target=_worker, args=(player, stop),
                              daemon=True, name="calendar-nudges")
        _loop["stop"] = stop
        _loop["thread"] = th
        th.start()


# ── entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "today").strip().lower()
    try:
        if not str(_setting("ics_url", "") or ""):
            return ("The calendar isn't connected yet, sir — open ⚙ → CALENDAR and "
                    "paste your calendar's iCal link (Google: Settings → your "
                    "calendar → 'Secret address in iCal format').")

        events, err = _refresh(force=(action == "refresh"))
        if err and not events:
            return f"I couldn't read your calendar — {err}."

        _ensure_loop(player)
        now = datetime.now()

        if action in ("today", "agenda", "schedule"):
            todays = _events_between(events, now - timedelta(minutes=1),
                                     now.replace(hour=23, minute=59, second=59))
            if not todays:
                return "Nothing else on the calendar today."
            return (f"Today ({len(todays)}):\n"
                    + "\n".join("• " + _fmt_event(e) for e in todays[:12]))

        if action == "tomorrow":
            start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
            end = start + timedelta(days=1)
            picked = _events_between(events, start, end)
            if not picked:
                return "Nothing on the calendar tomorrow."
            return ("Tomorrow:\n" + "\n".join("• " + _fmt_event(e) for e in picked[:12]))

        if action in ("week", "upcoming"):
            days = _as_int(parameters.get("days", 7), 7, 1, 30)
            picked = _events_between(events, now, now + timedelta(days=days))
            if not picked:
                return f"Nothing on the calendar for the next {days} day(s)."
            return (f"Next {days} day(s) ({len(picked)}):\n"
                    + "\n".join("• " + _fmt_event(e) for e in picked[:15]))

        if action == "next":
            upcoming = [e for e in events if e["end"] >= now]
            if not upcoming:
                return "Nothing coming up on the calendar."
            nxt = upcoming[0]
            mins = int((nxt["start"] - now).total_seconds() // 60)
            when = ("today" if mins < 60 * 24 else _fmt_event(nxt))
            return (f"Next up: {nxt['summary']}"
                    + (f" at {nxt['start']:%H:%M}" if not nxt["all_day"] else " (all day)")
                    + (f" — in {mins} minute(s)" if mins >= 0 else " — in progress")
                    + (f" at {nxt['location']}" if nxt["location"] else "") + ".")

        if action == "refresh":
            return (f"Calendar refreshed — {len(events)} event(s) in the next "
                    f"{_as_int(_setting('horizon_days', 14), 14, 1, 60)} days.")

        return f"Unknown calendar action '{action}'."
    except Exception as e:
        return f"Sir, the calendar plugin failed: {e}"
