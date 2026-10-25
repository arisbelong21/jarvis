"""
boss_key.py — one phrase hides everything, one phrase brings it back.

WHY THIS EXISTS
    The most human thing an assistant can do is get out of the way. When
    somebody walks up behind you, the screen full of sensitive content, the
    music playing and JARVIS's own panel are all liabilities. Saying "boss is
    coming" (or anything the model routes here) sweeps them away in one move:
    windows minimise, system volume drops to zero, and the HUD panel is
    replaced by a neutral work screen. "All clear" puts it back — exactly what
    was there before, because the state is captured BEFORE the change, not
    guessed afterwards.

WHY IT IS UNDOABLE BY CONSTRUCTION
    Everything it hides is recorded first (window titles + the volume level),
    so restore is a replay of the capture rather than a best effort. It also
    registers itself in the undo stack, which means the ordinary "undo" command
    reverses a boss key too.

WHAT IT DELIBERATELY DOES NOT DO
    It never closes anything, never kills a process, and never touches files —
    a panic button that can lose work is a trap. Minimise, mute, repaint. All
    three are reversible in one step, and the only thing that could fail
    (window enumeration on an OS without pygetwindow) degrades to muting and
    repainting instead of erroring out.
"""

import threading

PLUGIN = {
    "name": "boss_key",
    "description": (
        "Panic/hide button: instantly minimises the open windows, mutes the "
        "system volume and replaces JARVIS's on-screen panel with a neutral "
        "work screen — for when someone walks up and you do not want them to "
        "see what is on screen. Use for: 'boss is coming', 'hide everything', "
        "'panic screen', 'privacy mode', 'boss key'. Restore with 'all clear', "
        "'bring everything back', 'boss is gone'. This only hides and mutes — "
        "it never closes apps or deletes anything, and 'restore' puts every "
        "window and the volume back exactly as they were."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'hide' (default) minimises + mutes + repaints, "
                    "'restore' brings back what 'hide' changed, "
                    "'status' reports whether it is currently hidden."
                ),
            },
        },
        "required": [],
    },
}

_lock = threading.Lock()
_hidden: dict = {"active": False, "windows": [], "volume": None}


# ── helpers (every one of them a no-op when its dependency is absent) ────────

def _log(player, msg: str) -> None:
    if player:
        try:
            player.write_log(msg)
        except Exception:
            pass


def _panel(player, title: str, text: str) -> None:
    if player:
        try:
            player.show_content(title, text)
        except Exception:
            pass


def _visible_windows():
    """[(window, title)] for minimisable windows, [] when pygetwindow is absent."""
    try:
        import pygetwindow as gw
    except Exception:
        return []
    out = []
    try:
        for w in gw.getAllWindows():
            try:
                if w.visible and not w.isMinimized and (w.title or "").strip():
                    out.append((w, w.title))
            except Exception:
                continue
    except Exception:
        return []
    return out


def _volume_get():
    try:
        from actions.computer_settings import volume_get
        return volume_get()
    except Exception:
        return None


def _volume_set(value) -> None:
    try:
        from actions.computer_settings import volume_set
        volume_set(int(value))
    except Exception:
        pass


def _do_hide(player) -> str:
    with _lock:
        if _hidden["active"]:
            return "Everything is already hidden. Say 'all clear' to bring it back."

    wins = _visible_windows()
    titles = []
    for win, title in wins:
        try:
            win.minimize()
            titles.append(title)
        except Exception:
            continue                      # some windows refuse; the rest still go

    vol = _volume_get()
    if vol is not None:
        _volume_set(0)

    with _lock:
        _hidden.update({"active": True, "windows": titles, "volume": vol})

    # A neutral panel is the decoy: nothing sensitive, nothing to explain.
    _panel(player, "WORK MODE", "Spreadsheet open. Nothing to see here.")

    try:
        from core import undo as undo_stack
        undo_stack.push_undo("boss key", lambda: _do_restore(None))
    except Exception:
        pass

    hid = len(titles)
    parts = []
    parts.append(f"{hid} window{'s' if hid != 1 else ''} minimised" if hid
                 else "no windows could be minimised")
    if vol is not None:
        parts.append("volume muted")
    else:
        parts.append("volume could not be read")
    _log(player, "SYS: Boss key — " + ", ".join(parts) + ".")
    return ("Done — " + ", ".join(parts) +
            ". Say 'all clear' and I'll put everything back.")


def _do_restore(player) -> str:
    with _lock:
        if not _hidden["active"]:
            return "Nothing is hidden right now."
        titles = list(_hidden["windows"])
        vol = _hidden["volume"]
        _hidden.update({"active": False, "windows": [], "volume": None})

    restored = 0
    try:
        import pygetwindow as gw
        for title in titles:
            try:
                for win in gw.getWindowsWithTitle(title):
                    try:
                        win.restore()
                        win.activate()
                        restored += 1
                    except Exception:
                        try:
                            win.restore()
                            restored += 1
                        except Exception:
                            pass
                    break
            except Exception:
                continue
    except Exception:
        pass

    if vol is not None:
        _volume_set(vol)

    _log(player, f"SYS: Boss key cleared — {restored} window(s) restored.")
    return (f"All clear. {restored} window{'s' if restored != 1 else ''} "
            f"restored" + (", volume back to where it was." if vol is not None else "."))


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "hide").strip().lower()
    try:
        if action == "restore":
            return _do_restore(player)
        if action == "status":
            with _lock:
                active, n = _hidden["active"], len(_hidden["windows"])
            if not active:
                return "Nothing is hidden — the screen is as you left it."
            return f"Privacy mode is on: {n} window(s) minimised and the volume is muted."
        return _do_hide(player)
    except Exception as e:
        return f"Sir, the boss key failed: {e}"
