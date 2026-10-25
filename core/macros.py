"""
macros.py — user-taught shortcuts: "when I say X, do Y".

WHY THIS IS CORE AND NOT ONLY A PLUGIN
    A macro has to be reachable two ways, and only one of them goes through the
    model:

      1. Spoken. The model reads the macro table (kept in one rolling memory
         note, which is injected into every session) and calls the
         voice_macros tool when it hears a trigger. Nothing here is involved.

      2. Typed / sent from the phone. main.py::_on_text_command is the same
         door the desk text box and the Telegram bridge use, and it can check
         this store BEFORE handing the text to the model. That makes a typed
         trigger exact and instant instead of a round trip — and it is why the
         store lives in core/ rather than inside the plugin: main.py must be
         able to consult it even while the plugin itself is disabled.

MATCHING IS EXACT ON PURPOSE
    A trigger is a phrase the user chose. It is compared after normalising case,
    surrounding punctuation and inner whitespace — and then it must be the whole
    utterance. Fuzzy matching here would fire macros during ordinary sentences,
    which is exactly the surprise this project keeps designing against.

EXECUTION NEVER BLOCKS THE CALLER
    Typed commands arrive on the Qt thread; a macro may run an action that takes
    a minute. try_trigger() therefore starts the work on a daemon thread and
    returns immediately. The result is written to the activity log when it lands.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

_STORE = Path(__file__).resolve().parent.parent / "memory" / "macros.json"

_bind_lock = threading.Lock()
_bound: dict = {"action_registry": None, "plugin_registry": None,
                "player": None, "log": None}


def bind(action_registry=None, plugin_registry=None, player=None, log=None) -> None:
    """Called once at boot by main.py, same pattern as core.confirm.bind()."""
    with _bind_lock:
        _bound.update({"action_registry": action_registry,
                       "plugin_registry": plugin_registry,
                       "player": player, "log": log})


def _log(msg: str) -> None:
    fn = _bound.get("log")
    if fn:
        try:
            fn(msg)
        except Exception:
            pass


# ── store ────────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[.!?,;:]+$", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def load() -> dict:
    try:
        data = json.loads(_STORE.read_text(encoding="utf-8"))
        macros = data.get("macros")
        return macros if isinstance(macros, dict) else {}
    except Exception:
        return {}


def _save(macros: dict) -> None:
    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        _STORE.write_text(json.dumps({"macros": macros}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    except Exception:
        pass
    _refresh_memory(macros)


def _refresh_memory(macros: dict) -> None:
    """One rolling memory note listing the triggers, so the model recognises a
    spoken trigger in any language without the plugin being asked first."""
    try:
        from memory.memory_manager import remember
        if not macros:
            remember("voice_macros", "No voice shortcuts are taught.", "notes")
            return
        lines = [f"'{t}' → {spec.get('action')}" for t, spec in list(macros.items())[:20]]
        remember("voice_macros",
                 "User-taught voice shortcuts. When the user says one of these "
                 "triggers, call the voice_macros tool with action='run' and that "
                 "exact trigger: " + "; ".join(lines), "notes")
    except Exception:
        pass


def add(trigger: str, action: str, parameters: dict | None = None) -> str:
    trigger_n = _norm(trigger)
    if not trigger_n:
        return "The trigger phrase is empty."
    if not action:
        return "Which action should the trigger run?"
    macros = load()
    macros[trigger_n] = {"action": action.strip(),
                         "parameters": parameters or {},
                         "added": datetime.now().isoformat(timespec="seconds")}
    _save(macros)
    return f"Taught: when you say '{trigger_n}', I'll run '{action}'."


def remove(trigger: str) -> str:
    macros = load()
    key = _norm(trigger)
    if key not in macros:
        hits = [t for t in macros if key and key in t]
        if len(hits) == 1:
            key = hits[0]
        else:
            return f"No macro matches '{trigger}'."
    macros.pop(key, None)
    _save(macros)
    return f"Forgotten: '{key}'."


def clear() -> str:
    _save({})
    return "All voice shortcuts cleared."


def list_all() -> str:
    macros = load()
    if not macros:
        return "No voice shortcuts have been taught yet."
    lines = [f"'{t}' → {s.get('action')} {s.get('parameters') or ''}".strip()
             for t, s in macros.items()]
    return f"{len(macros)} shortcut(s):\n" + "\n".join(lines)


# ── execution ────────────────────────────────────────────────────────────────

def _run_spec(trigger: str, spec: dict) -> str:
    action = (spec.get("action") or "").strip()
    params = spec.get("parameters") or {}
    if not isinstance(params, dict):
        params = {}
    with _bind_lock:
        actions = _bound.get("action_registry")
        plugins = _bound.get("plugin_registry")
        player = _bound.get("player")

    if actions is not None and actions.has(action):
        try:
            return actions.run(action, dict(params),
                               {"player": player, "speak": None,
                                "response": None, "session_memory": None}) or "Done."
        except Exception as e:
            return f"Macro '{trigger}' failed: {e}"
    if plugins is not None and plugins.has(action):
        try:
            return plugins.run(action, dict(params), player=player) or "Done."
        except Exception as e:
            return f"Macro '{trigger}' failed: {e}"
    return (f"Macro '{trigger}' points at '{action}', which is not available "
            f"right now (missing action, or the plugin is disabled).")


def run_macro(trigger: str) -> str:
    """Synchronous execution — the plugin's own tool path."""
    key = _norm(trigger)
    macros = load()
    if key not in macros:
        hits = [t for t in macros if key and key in t]
        key = hits[0] if len(hits) == 1 else ""
    if not key or key not in macros:
        return f"No shortcut matches '{trigger}'."
    return _run_spec(key, macros[key])


def try_trigger(text: str) -> str | None:
    """Exact-match check for the typed/remote path. Returns a short ack when a
    macro matched (work continues on a daemon thread), None otherwise."""
    key = _norm(text)
    if not key:
        return None
    macros = load()
    spec = macros.get(key)
    if spec is None:
        return None
    threading.Thread(target=lambda: _log(f"JARVIS: {_run_spec(key, spec)}"),
                     daemon=True, name="macro-run").start()
    return f"Running your shortcut '{key}'."
