"""
home_automation.py — the house, by voice.

WHY HOME ASSISTANT AND NOT THE BULBS DIRECTLY
    Tuya's own API needs a developer account, an access key and a per-device
    key for every bulb, and the keys rotate. Home Assistant already holds all
    of that — including Tuya devices — behind one URL and one long-lived
    token, and it exposes the same REST API for every brand. So this plugin
    speaks to Home Assistant, and every device that HA knows (Tuya, Zigbee,
    Wi-Fi, IR) works through it without this file knowing the brand.

    Nothing is hardcoded about the house: the entity list is read from the
    server, and the user refers to things by the names they see in the HA
    dashboard ("the living room lamp"), not by entity ids.

WHAT IS GATED
    Turning a light off is a shrug. Unlocking a door, disarming an alarm or
    opening a garage is not. Actions whose domain is in the irreversible list
    go through the app's confirm gate — the same one the destructive file
    operations use — so they need an explicit yes on screen, not just a spoken
    sentence that might have been misheard. The list is a setting because
    which domains count as irreversible is the user's judgement, not mine.
"""

import json

PLUGIN = {
    "name": "home_automation",
    "description": (
        "Controls smart-home devices (lights, plugs, thermostats, locks, "
        "scenes) through a Home Assistant server: turn things on or off, set "
        "brightness, ask what state something is in, list the devices. Use "
        "for: 'turn off the living room lamp', 'set the bedroom light to 30 "
        "percent', 'is the garage door open', 'what lights are on', 'run the "
        "goodnight scene'. Irreversible things (locks, alarms) ask for "
        "confirmation on screen first. Requires a Home Assistant URL and token "
        "in the plugin settings."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'on', 'off', 'toggle', 'set' (brightness/temperature), "
                    "'status' (one device), 'list' (what exists / what is on), "
                    "'scene' (activate a scene)."
                ),
            },
            "device": {
                "type": "STRING",
                "description": (
                    "Which device, in the user's own words — 'living room lamp', "
                    "'garage'. Matched against the names Home Assistant knows."
                ),
            },
            "value": {
                "type": "NUMBER",
                "description": "For 'set': brightness 1-100, or a temperature in degrees.",
            },
            "attribute": {
                "type": "STRING",
                "description": "For 'set': 'brightness' (default) or 'temperature'.",
            },
        },
        "required": [],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "home_automation",
    "title": "🏠  HOME AUTOMATION (HOME ASSISTANT)",
    "fields": [
        {"key": "ha_url", "label": "Home Assistant URL", "type": "text",
         "placeholder": "http://homeassistant.local:8123"},
        {"key": "ha_token", "label": "Long-lived access token", "type": "password",
         "placeholder": "Profile → Security → Long-lived access tokens"},
        {"key": "confirm_irreversible",
         "label": "Ask for on-screen confirmation before locks/alarms/garage doors",
         "type": "toggle", "default": True},
        {"key": "irreversible_domains",
         "label": "Domains treated as irreversible (comma-separated)",
         "type": "text", "default": "lock, alarm_control_panel, cover",
         "placeholder": "lock, alarm_control_panel, cover"},
    ],
}

_NS = "home_automation"
_TIMEOUT = 12


# ── settings / transport ─────────────────────────────────────────────────────

def _setting(key, default=None):
    try:
        from memory.config_manager import get_plugin_setting
        val = get_plugin_setting(_NS, key, default)
        return default if val in (None, "") else val
    except Exception:
        return default


def _creds() -> tuple[str, str]:
    return (str(_setting("ha_url", "") or "").rstrip("/"),
            str(_setting("ha_token", "") or ""))


def _not_configured() -> str:
    return ("Home Assistant isn't set up yet, sir — open ⚙ → HOME AUTOMATION "
            "and give me the server URL and a long-lived access token.")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_states(url: str, token: str):
    try:
        import requests
        r = requests.get(f"{url}/api/states", headers=_headers(token), timeout=_TIMEOUT)
        if r.status_code == 401:
            return None, "the token was rejected (401)"
        if r.status_code >= 400:
            return None, f"the server answered HTTP {r.status_code}"
        data = r.json()
        return (data if isinstance(data, list) else []), ""
    except Exception as e:
        return None, f"the server could not be reached ({type(e).__name__})"


def _call_service(url: str, token: str, domain: str, service: str,
                  entity_id: str, extra: dict | None = None):
    try:
        import requests
        body = {"entity_id": entity_id}
        if extra:
            body.update(extra)
        r = requests.post(f"{url}/api/services/{domain}/{service}",
                          headers=_headers(token), timeout=_TIMEOUT,
                          data=json.dumps(body))
        if r.status_code >= 400:
            return f"the server answered HTTP {r.status_code}"
        return ""
    except Exception as e:
        return f"the call failed ({type(e).__name__})"


# ── entity matching ──────────────────────────────────────────────────────────

_CONTROL_DOMAINS = ("light", "switch", "fan", "media_player", "input_boolean",
                    "climate", "lock", "cover", "scene", "script", "automation")


def _friendly(st: dict) -> str:
    return str((st.get("attributes") or {}).get("friendly_name") or "").strip()


def _match(states: list[dict], want: str) -> list[dict]:
    want_l = (want or "").strip().lower()
    if not want_l:
        return []
    exact, partial = [], []
    for st in states:
        eid = str(st.get("entity_id") or "")
        domain = eid.split(".", 1)[0]
        if domain not in _CONTROL_DOMAINS:
            continue
        name = _friendly(st).lower()
        if want_l == name or want_l == eid.lower():
            exact.append(st)
        elif want_l in name or want_l in eid.lower():
            partial.append(st)
    return exact or partial


def _describe(st: dict) -> str:
    return f"{_friendly(st) or st.get('entity_id')} is {st.get('state')}"


# ── the gate ─────────────────────────────────────────────────────────────────

def _irreversible_domains() -> set[str]:
    raw = str(_setting("irreversible_domains", "lock, alarm_control_panel, cover") or "")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def _needs_confirm(domain: str) -> bool:
    if not _setting("confirm_irreversible", True):
        return False
    return domain in _irreversible_domains()


def _gated(domain: str, title: str, detail: str, fn) -> str:
    """Route an irreversible call through the app's confirm gate."""
    try:
        from core import confirm
        return confirm.request(key=f"home:{domain}", title=title, detail=detail,
                               run=fn) or "Waiting for your confirmation."
    except Exception:
        return ("I need a confirmation for that one, but the confirmation window "
                "isn't available right now — I won't do it without it.")


# ── entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "list").strip().lower()
    try:
        url, token = _creds()
        if not url or not token:
            return _not_configured()

        states, err = _get_states(url, token)
        if states is None:
            return f"I couldn't reach Home Assistant — {err}."

        if action in ("list", "devices"):
            rows = []
            for st in states:
                eid = str(st.get("entity_id") or "")
                if eid.split(".", 1)[0] not in _CONTROL_DOMAINS:
                    continue
                rows.append(f"{_friendly(st) or eid} ({eid}) — {st.get('state')}")
            if not rows:
                return "Home Assistant is reachable but reports no controllable devices."
            shown = rows[:40]
            return (f"{len(rows)} device(s)"
                    + (" (first 40 shown)" if len(rows) > 40 else "") + ":\n"
                    + "\n".join(shown))

        if action in ("status", "state"):
            hits = _match(states, parameters.get("device") or "")
            if not hits:
                return f"I couldn't find a device called '{parameters.get('device')}'."
            return "; ".join(_describe(s) for s in hits[:3])

        if action == "scene":
            hits = [s for s in _match(states, parameters.get("device") or "")
                    if str(s.get("entity_id", "")).startswith("scene.")]
            if not hits:
                return f"I couldn't find a scene called '{parameters.get('device')}'."
            target = hits[0]
            eid = str(target["entity_id"])
            err2 = _call_service(url, token, "scene", "turn_on", eid)
            if err2:
                return f"Activating the scene failed — {err2}."
            return f"Scene '{_friendly(target) or eid}' activated."

        hits = _match(states, parameters.get("device") or "")
        if not hits:
            return (f"I couldn't find a device called '{parameters.get('device')}'. "
                    f"Ask me to list the devices if you want to see what I know.")
        target = hits[0]
        eid = str(target["entity_id"])
        domain = eid.split(".", 1)[0]
        name = _friendly(target) or eid

        if action == "set":
            value = parameters.get("value")
            attribute = (parameters.get("attribute") or "brightness").strip().lower()
            if value is None:
                return f"What should I set {name} to?"
            if domain == "climate" or attribute.startswith("temp"):
                service, extra = "set_temperature", {"temperature": float(value)}
            else:
                pct = max(1, min(100, int(float(value))))
                service, extra = "turn_on", {"brightness_pct": pct}

            def _do_set():
                e = _call_service(url, token, domain, service, eid, extra)
                return f"Failed: {e}" if e else f"Done — {name} set."

            if _needs_confirm(domain):
                return _gated(domain, f"Set {name}",
                              f"Set {name} ({eid}) to {value}.", _do_set)
            return _do_set()

        service = {"on": "turn_on", "off": "turn_off",
                   "toggle": "toggle"}.get(action)
        if service is None:
            return f"Unknown home action '{action}'."

        def _do_power():
            e = _call_service(url, token, domain, service, eid)
            return f"Failed: {e}" if e else f"Done — {name} {action}."

        if _needs_confirm(domain):
            return _gated(domain, f"{action.upper()} {name}",
                          f"About to turn {action} {name} ({eid}).", _do_power)
        return _do_power()
    except Exception as e:
        return f"Sir, the home automation plugin failed: {e}"
