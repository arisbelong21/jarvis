"""
voice_macros.py — teach JARVIS a shortcut in one sentence.

"Teach a shortcut: when I say 'kerja mode', open VS Code and start the
pomodoro timer." From then on the phrase runs that action — by voice through
this tool (the model recognises the trigger from the memory note the store
keeps), and instantly for typed or phone commands, where main.py consults the
same store before it bothers the model at all.

The store, the matching rule and the execution all live in core/macros.py;
this file is the voice interface to them. That split is deliberate: the typed
path must keep working even when this plugin is switched off.
"""

from core import macros as _macros

PLUGIN = {
    "name": "voice_macros",
    "description": (
        "Teaches JARVIS custom voice shortcuts: 'when I say X, do Y'. Use for: "
        "'teach a shortcut', 'when I say X do Y', 'make X run Y', 'add a voice "
        "command', 'what shortcuts do I have', 'forget the X shortcut'. The "
        "action it points at must be an existing tool name (open_app, "
        "computer_settings, code_helper, a plugin's name, …). When the user "
        "later SAYS a taught trigger phrase, call this tool with action='run' "
        "and that exact trigger to execute it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'add' (default) teach a new shortcut, 'list' show them, "
                    "'remove' forget one, 'run' execute one now (used when the "
                    "user speaks a taught trigger), 'clear' forget all."
                ),
            },
            "trigger": {
                "type": "STRING",
                "description": (
                    "The phrase the user will say, e.g. 'kerja mode' — for "
                    "'add', 'remove' and 'run'."
                ),
            },
            "target_action": {
                "type": "STRING",
                "description": (
                    "For 'add': the exact tool name to run, e.g. 'open_app', "
                    "'computer_settings', 'boss_key'."
                ),
            },
            "target_parameters": {
                "type": "STRING",
                "description": (
                    "For 'add': the arguments for that tool, as a JSON object "
                    "string, e.g. '{\"app_name\": \"chrome\"}'."
                ),
            },
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "add").strip().lower()
    try:
        if action == "list":
            return _macros.list_all()

        if action == "clear":
            return _macros.clear()

        if action == "remove":
            trigger = parameters.get("trigger") or parameters.get("target_action") or ""
            if not trigger:
                return "Which shortcut should I forget?"
            return _macros.remove(trigger)

        if action in ("run", "trigger", "execute"):
            trigger = parameters.get("trigger") or ""
            if not trigger:
                return "Which shortcut should I run?"
            result = _macros.run_macro(trigger)
            if player:
                try:
                    player.write_log(f"JARVIS: {result}")
                except Exception:
                    pass
            return result

        # -------- ADD --------
        trigger = (parameters.get("trigger") or "").strip()
        target = (parameters.get("target_action") or "").strip()
        if not trigger:
            return "What phrase should trigger the shortcut, sir?"
        if not target:
            return "And which action should that phrase run?"

        raw_params = parameters.get("target_parameters")
        target_params: dict = {}
        if isinstance(raw_params, dict):
            target_params = raw_params
        elif isinstance(raw_params, str) and raw_params.strip():
            import json
            try:
                parsed = json.loads(raw_params)
                if isinstance(parsed, dict):
                    target_params = parsed
            except Exception:
                return (f"I couldn't read the arguments '{raw_params}' — they "
                        f"need to be JSON, like {{\"app_name\": \"chrome\"}}.")

        result = _macros.add(trigger, target, target_params)
        if player:
            try:
                player.write_log(f"JARVIS: {result}")
            except Exception:
                pass
        return result
    except Exception as e:
        return f"Sir, the voice macros plugin failed: {e}"
