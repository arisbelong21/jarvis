"""
price_tracker.py — watch a product page, get told when the price drops.

HOW IT READS A PRICE WITHOUT A BROWSER
    Most shop pages carry the price in machine-readable form, so the cheap path
    is taken first and the expensive one only if it fails:

      1. JSON-LD (schema.org Product/Offer) — what shops publish for Google.
      2. An og:/product: meta tag.
      3. A currency-looking string in the visible text (Rp 1.234.567, $19.99,
         €1.234,56 …), parsed with the separator rules that actually appear in
         the wild — Indonesian pages write 1.234.567,00 where US pages write
         1,234,567.00, and reading one as the other is a 1000x error.
      4. Only if all three find nothing: one FAST-tier Gemini call over a slice
         of the page text. That covers pages that render the price in script.

    A number that cannot be trusted is never stored: if the page returns
    nothing parseable, the item keeps its previous value and the log says the
    check failed, rather than inventing a price and alerting on it.

WHEN IT SPEAKS
    A target price means "tell me when it goes at or below this". With no
    target it reports a drop of 5% or more against the price it first saw. It
    will not repeat the same alert: the price it last alerted on is remembered,
    and a new alert needs a new (lower) number.
"""

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from core import gemini as _gemini

STATE_FILE = Path(__file__).resolve().parent.parent / "memory" / "price_watch.json"

PLUGIN = {
    "name": "price_tracker",
    "description": (
        "Watches product pages and alerts the user out loud when the price "
        "drops to their target (or by 5% if they gave no target). Use for: "
        "'watch this price', 'tell me when this gets cheaper', 'track the price "
        "of X', 'what's the price of the thing I'm tracking', 'stop tracking "
        "X'. It checks in the background every hour. This is for a PRICE on a "
        "shop page — for a general on-screen condition use vision_watcher."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'add' (default) start tracking a product URL, 'remove' stop "
                    "tracking one, 'list' show what is tracked, 'check' look "
                    "right now instead of waiting."
                ),
            },
            "url": {
                "type": "STRING",
                "description": "For 'add': the product page URL.",
            },
            "name": {
                "type": "STRING",
                "description": "For 'add': a short name for the item (optional).",
            },
            "target_price": {
                "type": "NUMBER",
                "description": (
                    "For 'add': alert when the price is at or below this. "
                    "Optional — without it a 5% drop triggers the alert."
                ),
            },
            "item_id": {
                "type": "STRING",
                "description": "For 'remove': which item (id, name or a word from the URL).",
            },
        },
        "required": [],
    },
}

_lock = threading.Lock()
_loop = {"thread": None, "stop": None}
_MAX_ITEMS = 10
_CHECK_EVERY_S = 3600          # hourly
_DROP_ALERT_PCT = 5.0

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ── store ────────────────────────────────────────────────────────────────────

def _load() -> list[dict]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        items = data.get("items")
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2),
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


# ── price parsing ────────────────────────────────────────────────────────────

def _normalise_number(raw: str):
    """'1.234.567,00' → 1234567.0 ; '1,234,567.00' → 1234567.0 ; '19,99' → 19.99.

    Rule: the LAST separator wins as the decimal point when it is followed by
    one or two digits; otherwise every separator is a thousands mark. Reading
    these the other way round turns Rp 1.234.567 into Rp 1.23, which is how a
    price watcher alerts on nonsense."""
    s = raw.strip()
    m = re.match(r"^(\d[\d\s.,]*)$", s)
    if not m:
        return None
    digits = re.sub(r"[^\d.,]", "", s)
    if not digits:
        return None
    last_dot = digits.rfind(".")
    last_comma = digits.rfind(",")
    last_sep = max(last_dot, last_comma)
    if last_sep == -1:
        try:
            return float(digits)
        except ValueError:
            return None
    tail = digits[last_sep + 1:]
    if len(tail) in (1, 2) and tail.isdigit():
        head = re.sub(r"[.,]", "", digits[:last_sep])
        try:
            return float(f"{head}.{tail}")
        except ValueError:
            return None
    try:
        return float(re.sub(r"[.,]", "", digits))
    except ValueError:
        return None


_JSONLD_RE = re.compile(r'"price"\s*:\s*"?([\d.,]+)"?', re.I)
_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:product:price:amount|og:price:amount)["\']'
    r'[^>]+content=["\']([\d.,]+)["\']', re.I)
_CURRENCY_RE = re.compile(
    r"(?:Rp|IDR|USD|\$|€|£)\s?([\d][\d.,]{2,})"
    r"|([\d][\d.,]{2,})\s?(?:rupiah|IDR|USD)", re.I)


def _price_from_html(html: str):
    for rx in (_META_RE, _JSONLD_RE):
        for m in rx.finditer(html):
            val = _normalise_number(m.group(1))
            if val and val > 0:
                return val, f"page metadata ({rx.pattern[:18]}…)"
    for m in _CURRENCY_RE.finditer(html):
        raw = m.group(1) or m.group(2) or ""
        val = _normalise_number(raw)
        if val and val > 0:
            return val, "price text on the page"
    return None, ""


def _price_from_model(text: str):
    try:
        prompt = (
            "From this web page text, find the CURRENT price of the product. "
            "Reply with ONLY the number, digits and decimal separator, no "
            "currency symbol, no thousands separators (e.g. 1234.50). "
            "If no price is present, reply exactly: NONE\n\n"
            + text[:6000]
        )
        resp = _gemini.call(prompt, tier=_gemini.FAST, timeout_ms=20000)
        if resp is None:
            return None
        out = (getattr(resp, "text", "") or "").strip()
        if "NONE" in out.upper():
            return None
        m = re.search(r"[\d][\d.,]*", out)
        return _normalise_number(m.group(0)) if m else None
    except Exception:
        return None


def _fetch_price(url: str):
    """(price, how) or (None, reason). Never raises."""
    try:
        import requests
        r = requests.get(url, timeout=25, headers={
            "User-Agent": _UA,
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        })
        if r.status_code >= 400:
            return None, f"the page answered HTTP {r.status_code}"
        html = r.text or ""
    except Exception as e:
        return None, f"the page could not be fetched ({type(e).__name__})"

    price, how = _price_from_html(html)
    if price:
        return price, how
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    price = _price_from_model(text[:6000])
    if price:
        return price, "read from the page text by the model"
    return None, "no price could be read from the page"


def _fmt(price, currency: str = "Rp") -> str:
    try:
        if currency == "Rp":
            return "Rp" + f"{price:,.0f}".replace(",", ".")
        return f"{price:,.2f}"
    except Exception:
        return str(price)


# ── background loop ──────────────────────────────────────────────────────────

def _check_item(item: dict, player) -> tuple[bool, str]:
    """Returns (changed, message). Alerts when the trigger is met."""
    price, how = _fetch_price(item["url"])
    if price is None:
        return False, f"{item.get('name') or item['url']}: {how}"
    old = item.get("last_price")
    item["last_price"] = price
    item["last_check"] = datetime.now().isoformat(timespec="seconds")
    item.setdefault("history", []).append([item["last_check"], price])
    item["history"] = item["history"][-30:]

    target = item.get("target_price")
    alerted = item.get("last_alerted")
    fired = False
    if target and price <= float(target):
        if alerted is None or price < float(alerted):
            fired = True
    elif not target and old:
        try:
            drop = (float(old) - price) / float(old) * 100.0
            if drop >= _DROP_ALERT_PCT and (alerted is None or price < float(alerted)):
                fired = True
        except Exception:
            fired = False

    if fired:
        item["last_alerted"] = price
        name = item.get("name") or item["url"]
        target_txt = f"your target was {_fmt(target)}" if target else "that is a real drop"
        _say(player, f"Tell the user in one short sentence: the price of {name} "
                     f"is now {_fmt(price)} — {target_txt}. Read from the page "
                     f"({how}).")
        _log(player, f"JARVIS: Price alert — {name} is now {_fmt(price)}.")
    return True, f"{item.get('name') or item['url']}: {_fmt(price)}" + ("  ← alert sent" if fired else "")


def _worker(player, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            if stop.wait(_CHECK_EVERY_S):
                break
            items = _load()
            if not items:
                break
            for item in items:
                try:
                    _check_item(item, player)
                except Exception:
                    continue
            _save(items)
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
                              daemon=True, name="price-tracker")
        _loop["stop"] = stop
        _loop["thread"] = th
        th.start()


# ── entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "add").strip().lower()
    try:
        if action in ("list", "status"):
            items = _load()
            if not items:
                return "I'm not tracking any prices."
            lines = []
            for it in items:
                cur = it.get("last_price")
                tgt = it.get("target_price")
                lines.append(
                    f"{it['id']}: {it.get('name') or it['url']} — "
                    + (f"now {_fmt(cur)}" if cur else "no price read yet")
                    + (f", target {_fmt(tgt)}" if tgt else ", alerts on a 5% drop"))
            return f"{len(items)} tracked:\n" + "\n".join(lines)

        if action in ("remove", "stop"):
            key = (parameters.get("item_id") or parameters.get("name") or "").strip().lower()
            items = _load()
            if not items:
                return "There is nothing being tracked."
            keep, removed = [], None
            for it in items:
                hay = f"{it['id']} {it.get('name','')} {it['url']}".lower()
                if removed is None and key and key in hay:
                    removed = it
                else:
                    keep.append(it)
            if removed is None:
                return f"I couldn't match '{key}' to a tracked item."
            _save(keep)
            return f"Stopped tracking {removed.get('name') or removed['url']}."

        if action in ("check", "check_now"):
            items = _load()
            if not items:
                return "There is nothing being tracked yet."
            out = []
            for it in items[:5]:
                try:
                    _changed, msg = _check_item(it, player)
                    out.append(msg)
                except Exception as e:
                    out.append(f"{it.get('name') or it['url']}: check failed ({e})")
            _save(items)
            return "Checked now — " + "; ".join(out)

        # -------- ADD --------
        url = (parameters.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return "I need the full product link, starting with http, sir."
        items = _load()
        if len(items) >= _MAX_ITEMS:
            return (f"I'm already tracking {_MAX_ITEMS} items — the limit. "
                    f"Remove one first.")
        for it in items:
            if it["url"] == url:
                return f"That link is already being tracked ({it['id']})."

        try:
            target = float(parameters.get("target_price")) if parameters.get("target_price") is not None else None
        except (TypeError, ValueError):
            target = None

        name = (parameters.get("name") or "").strip()
        item = {
            "id": f"p{int(time.time()) % 100000}",
            "name": name,
            "url": url,
            "target_price": target,
            "last_price": None,
            "last_alerted": None,
            "last_check": "",
            "history": [],
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        price, how = _fetch_price(url)
        if price is not None:
            item["last_price"] = price
            item["last_check"] = datetime.now().isoformat(timespec="seconds")
            item["history"].append([item["last_check"], price])
        items.append(item)
        _save(items)
        _ensure_loop(player)

        if price is None:
            return (f"Tracking {name or url} — but I couldn't read a price yet "
                    f"({how}). I'll keep trying every hour.")
        base = (f"Tracking {name or url}: it is {_fmt(price)} right now")
        if target:
            base += f", and I'll tell you when it reaches {_fmt(target)}"
        else:
            base += f", and I'll tell you if it drops {int(_DROP_ALERT_PCT)}% or more"
        return base + "."
    except Exception as e:
        return f"Sir, the price tracker failed: {e}"
