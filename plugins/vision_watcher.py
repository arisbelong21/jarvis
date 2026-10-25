"""
vision_watcher.py — "tell me when X happens on screen".

WHY THIS EXISTS
    JARVIS's own system prompt admits the limit this closes: sight is not
    continuous. He can look at the screen when asked, and chat_takeover proved
    that watching it on a timer works — but only for its one hardcoded job
    (replying to chats). This plugin turns that into a general instruction the
    user gives in their own words: "let me know when the download finishes",
    "tell me if the price on this page drops", "ping me when the render is
    done". Each watcher is a condition plus how often to look.

HOW IT LOOKS WITHOUT BURNING TOKENS
    One screenshot per watcher per interval (default every 30 s), downscaled
    before it is sent, and a question shaped so the answer is one word plus a
    line — YES/NO and a reason. The image is never stored; only the verdict and
    a cooldown clock are. The cooldown (default 5 min) is what stops a
    condition that stays true from becoming a chant.

WHAT IT COSTS WHEN IDLE
    Nothing. The loop exits the moment the last watcher is removed, and a
    watcher list that is empty means no thread, no screenshots, no calls. The
    saved list survives a restart; the loop does not auto-start with the app
    (plugins have no startup hook) — say "resume the watchers" to arm them
    again, which is deliberate: a plugin file dropped in a folder must not
    silently begin taking screenshots.
"""

import base64
import io
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from core import gemini as _gemini

STATE_FILE = Path(__file__).resolve().parent.parent / "memory" / "vision_watchers.json"

PLUGIN = {
    "name": "vision_watcher",
    "description": (
        "Watches the screen on a timer and ALERTS the user out loud when a "
        "condition they described becomes true — 'tell me when the download "
        "finishes', 'let me know when the price drops', 'ping me when this "
        "finishes rendering'. Use for: 'watch for X', 'tell me when X happens', "
        "'keep an eye on the screen and let me know if ...', 'stop watching'. "
        "This is for a condition on SCREEN that should trigger an ALERT later — "
        "for a one-off look at the screen right now use the screen tool "
        "instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'add' (default) start watching a condition, 'remove' stop "
                    "one (by id or keyword), 'list' show the active watchers, "
                    "'check' look right now and answer immediately, "
                    "'resume' re-arm all saved watchers, 'stop' stop everything."
                ),
            },
            "condition": {
                "type": "STRING",
                "description": (
                    "For 'add': the visual condition in the user's own words, "
                    "e.g. 'the download progress bar shows 100%' or 'a red error "
                    "message is visible'."
                ),
            },
            "note": {
                "type": "STRING",
                "description": (
                    "For 'add': what to say when it happens. Optional — the "
                    "condition itself is used when this is empty."
                ),
            },
            "interval_seconds": {
                "type": "INTEGER",
                "description": "For 'add': how often to look (default 30, min 10).",
            },
            "watcher_id": {
                "type": "STRING",
                "description": "For 'remove'/'check': which watcher (id, or any keyword from its condition).",
            },
        },
        "required": [],
    },
}

_lock = threading.Lock()
_loop = {"thread": None, "stop": None}
_MAX_WATCHERS = 5
_MIN_INTERVAL = 10
_MAX_IMAGE_WIDTH = 1024


# ── state ────────────────────────────────────────────────────────────────────

def _load() -> list[dict]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        ws = data.get("watchers")
        return ws if isinstance(ws, list) else []
    except Exception:
        return []


def _save(watchers: list[dict]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"watchers": watchers}, ensure_ascii=False),
                              encoding="utf-8")
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


# ── the one screen grab + one question ───────────────────────────────────────

def _screenshot_b64() -> str | None:
    """Downscaled PNG of the primary screen, base64. None when unavailable."""
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.rgb)
        if img.width > _MAX_IMAGE_WIDTH:
            h = int(img.height * _MAX_IMAGE_WIDTH / img.width)
            img = img.resize((_MAX_IMAGE_WIDTH, h))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _ask_vision(b64: str, condition: str) -> tuple[bool, str]:
    """(matched, one-line reason). Fails closed: an unreachable model is not a
    match — a watcher that fires on error is worse than one that stays quiet."""
    try:
        from google.genai import types
        part = types.Part.from_bytes(data=base64.b64decode(b64), mime_type="image/png")
        prompt = (
            "You are checking ONE condition on a screenshot of a computer screen.\n"
            f"CONDITION: {condition}\n\n"
            "Answer in exactly this shape:\n"
            "MATCH: YES or NO\n"
            "WHY: one short sentence\n"
            "Say YES only if the screen clearly shows the condition. If the "
            "condition is about something being finished/complete (a download, a "
            "render, a progress bar at 100%), YES only when it visibly is."
        )
        resp = _gemini.call([prompt, part], tier=_gemini.SMART, timeout_ms=30000)
        if resp is None:
            return False, "the model was unreachable"
        text = (getattr(resp, "text", "") or "").strip()
        upper = text.upper()
        matched = "MATCH: YES" in upper or upper.startswith("YES")
        why = ""
        for line in text.splitlines():
            if line.upper().startswith("WHY:"):
                why = line.split(":", 1)[1].strip()
                break
        return matched, (why or text[:120])
    except Exception as e:
        return False, f"vision check failed: {e}"


# ── background loop ──────────────────────────────────────────────────────────

def _worker(player, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            if stop.wait(5):
                break
            now = time.time()
            watchers = _load()
            if not watchers:
                break
            dirty = False
            for w in watchers:
                try:
                    due = (now - float(w.get("last_check") or 0)) >= float(w.get("interval_s") or 30)
                    if not due:
                        continue
                    b64 = _screenshot_b64()
                    if not b64:
                        w["last_check"] = now
                        dirty = True
                        continue
                    matched, why = _ask_vision(b64, w["condition"])
                    w["last_check"] = now
                    dirty = True
                    if matched and (now - float(w.get("last_alert") or 0)) >= float(w.get("cooldown_s") or 300):
                        w["last_alert"] = now
                        note = w.get("note") or w["condition"]
                        _say(player,
                             f"Tell the user this right now, in one short sentence: the "
                             f"thing they asked you to watch for has happened — {note}. "
                             f"(What you saw: {why})")
                        _log(player, f"JARVIS: Watcher fired — {note}")
                        try:
                            if player:
                                player.show_content("👁 VISION WATCHER",
                                                    f"{note}\n\n{why}\n\n{datetime.now():%H:%M:%S}")
                        except Exception:
                            pass
                except Exception:
                    continue
            if dirty:
                _save(watchers)
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
                              daemon=True, name="vision-watcher")
        _loop["stop"] = stop
        _loop["thread"] = th
        th.start()


def _stop_loop() -> None:
    with _lock:
        stop = _loop["stop"]
    if stop:
        stop.set()


# ── entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "add").strip().lower()
    try:
        if action in ("list", "status"):
            ws = _load()
            if not ws:
                return "No vision watchers are set."
            lines = [f"{w['id']}: {w['condition']}"
                     + (f" (says: {w['note']})" if w.get("note") else "")
                     for w in ws]
            running = "watching now" if (_loop["thread"] and _loop["thread"].is_alive()) else "paused"
            return f"{len(ws)} watcher(s), {running}:\n" + "\n".join(lines)

        if action in ("stop", "stop_all", "clear"):
            _stop_loop()
            _save([])
            return "All vision watchers stopped and cleared."

        if action == "remove":
            key = (parameters.get("watcher_id") or parameters.get("condition") or "").strip().lower()
            ws = _load()
            if not ws:
                return "There are no watchers to remove."
            keep, removed = [], None
            for w in ws:
                if removed is None and key and (key == w["id"].lower() or key in w["condition"].lower()):
                    removed = w
                else:
                    keep.append(w)
            if removed is None:
                return f"I couldn't match a watcher to '{key}'."
            _save(keep)
            if not keep:
                _stop_loop()
            return f"Stopped watching: {removed['condition']}"

        if action in ("resume", "start"):
            ws = _load()
            if not ws:
                return "There are no saved watchers to resume."
            _ensure_loop(player)
            return f"Watching again: {len(ws)} condition(s)."

        if action == "check":
            ws = _load()
            if not ws:
                return "No vision watchers are set. Tell me a condition to watch for first."
            key = (parameters.get("watcher_id") or "").strip().lower()
            targets = [w for w in ws if not key or key == w["id"].lower() or key in w["condition"].lower()]
            if not targets:
                return f"I couldn't match a watcher to '{key}'."
            b64 = _screenshot_b64()
            if not b64:
                return "I couldn't capture the screen, sir."
            out = []
            for w in targets[:3]:
                matched, why = _ask_vision(b64, w["condition"])
                out.append(f"{'YES' if matched else 'not yet'} — {w['condition']} ({why})")
            return "Right now: " + "; ".join(out)

        # -------- ADD --------
        condition = (parameters.get("condition") or "").strip()
        if not condition:
            return "What should I watch for on screen, sir?"
        ws = _load()
        if len(ws) >= _MAX_WATCHERS:
            return (f"I'm already watching {_MAX_WATCHERS} conditions — the limit. "
                    f"Remove one first ('stop watching ...').")
        interval = parameters.get("interval_seconds")
        try:
            interval = max(_MIN_INTERVAL, int(interval))
        except (TypeError, ValueError):
            interval = 30
        wid = f"w{int(time.time()) % 100000}"
        ws.append({
            "id": wid,
            "condition": condition,
            "note": (parameters.get("note") or "").strip(),
            "interval_s": interval,
            "cooldown_s": 300,
            "last_check": 0.0,
            "last_alert": 0.0,
            "created": datetime.now().isoformat(timespec="seconds"),
        })
        _save(ws)
        _ensure_loop(player)
        return (f"Watching for: {condition}. I'll check every {interval} seconds "
                f"and tell you the moment it happens.")
    except Exception as e:
        return f"Sir, the vision watcher failed: {e}"
