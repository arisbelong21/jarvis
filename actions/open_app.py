import time
import subprocess
import platform
import shutil

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

# Audit 26 Okt — verifikasi berbasis kontrak. Import dijaga: bila core tak
# dapat diimpor (mis. module dipakai standalone), tool tetap jalan normal dan
# hanya tidak menempel kontrak (verify() lalu pakai heuristik lama).
try:
    from core import contract as _contract
except Exception:                                # noqa: BLE001  # pragma: no cover
    _contract = None


def _verdict(text: str, ok, **ev) -> str:
    """ok=True|False|None → tempel kontrak hasil open_app. Aman bila core absen."""
    if _contract is None:
        return text
    fn = {True: _contract.ok, False: _contract.fail}.get(ok, _contract.unknown)
    return fn(text, tool="open_app", **ev)

_APP_ALIASES: dict[str, dict[str, str]] = {

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "x-terminal-emulator"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    # Editors and agents people actually ask for by name. "opencode" is its
    # own program (an npm CLI), NOT a misspelling of "code" — before this
    # entry the substring matcher sent it to `code`, and on a machine where
    # Cursor owns `code` in PATH the wrong editor opened while the assistant
    # said it had opened the one that was asked for.
    "opencode":           {"Windows": "opencode",                "Darwin": "opencode",             "Linux": "opencode"},
    "open code":          {"Windows": "opencode",                "Darwin": "opencode",             "Linux": "opencode"},
    "cursor":             {"Windows": "cursor",                  "Darwin": "Cursor",               "Linux": "cursor"},
    "windsurf":           {"Windows": "windsurf",                "Darwin": "Windsurf",             "Linux": "windsurf"},
    "zed":                {"Windows": "zed",                     "Darwin": "Zed",                  "Linux": "zed"},
    "trae":               {"Windows": "trae",                    "Darwin": "Trae",                 "Linux": "trae"},
}


def _words(text: str) -> set[str]:
    """The words in a name, lowercased. Used for whole-word matching only."""
    import re as _re
    return {w for w in _re.split(r"[^\w+#.]+", (text or "").lower()) if w}


def _normalize(raw: str) -> str:
    """The executable this name means, matched on WHOLE WORDS.

    The previous version asked `alias_key in key`, so "opencode" contained
    "code" and was sent to the `code` launcher — which on this machine is
    Cursor's shim, not VS Code. A name is now only recognised when every word
    of the alias appears as a word of the request; the longest alias wins, so
    "visual studio code" still beats "code".
    """
    key = " ".join((raw or "").lower().strip().split())

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    words = _words(key)
    best: tuple[int, dict] | None = None
    for alias_key, os_map in _APP_ALIASES.items():
        alias_words = _words(alias_key)
        if alias_words and alias_words <= words:
            if best is None or len(alias_words) > best[0]:
                best = (len(alias_words), os_map)
    return best[1].get(_SYSTEM, raw) if best else raw


def _pick_executable(name: str) -> str:
    """The absolute path to run for `name`, avoiding another app's shim.

    Cursor installs `code.cmd` and puts it ahead of VS Code in PATH, so
    `shutil.which("code")` hands back Cursor and the wrong editor opens.
    Every match is inspected and one whose path mentions another editor is
    passed over while a clean candidate remains.
    """
    import os

    found: list[str] = []
    # GrokBot: di Windows, berkas TANPA ekstensi (mis. shim bash npm
    # `%APPDATA%\npm\opencode`) tidak bisa dijalankan — dulu ia dicoba
    # PERTAMA, sehingga "opencode" membuka dialog "Open with"/aplikasi lain.
    if os.name == "nt":
        exts = [e if e.startswith(".") else "." + e
                for e in (os.environ.get("PATHEXT") or ".EXE;.CMD;.BAT").split(";") if e]
    else:
        exts = [""]
    for folder in (os.environ.get("PATH") or "").split(os.pathsep):
        folder = folder.strip('"')
        if not folder:
            continue
        for ext in exts:
            candidate = os.path.join(folder, name + ext.lower())
            if os.path.isfile(candidate):
                found.append(candidate)
            candidate = os.path.join(folder, name + ext.upper())
            if os.path.isfile(candidate) and candidate not in found:
                found.append(candidate)
    if not found:
        return ""
    if name.lower() == "code":
        clean = [p for p in found if "cursor" not in p.lower()]
        if clean:
            return clean[0]
    return found[0]


def _window_titles() -> set[str]:
    """Titles of the windows on screen now, or an empty set if unreadable."""
    try:
        import pygetwindow
        return {t for t in pygetwindow.getAllTitles() if t and t.strip()}
    except Exception:
        return set()


def _wait_new_window(before: set[str], seconds: float = 6.0) -> str:
    """The first window that was not there before, or ''.

    This is the evidence the answer is built from: a launcher saying "ok" is
    not proof that anything appeared.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.5)
        new = _window_titles() - before
        if new:
            return sorted(new)[0]
    return ""

def _judge_window(name: str, title: str) -> str:
    """What the new window actually is, relative to the name that was asked for.

    "ok"     the title carries the name — this is the app.
    "search" Windows found nothing and opened a web search instead; that is a
             failed launch wearing a window.
    "other"  a window appeared but nothing ties it to the name.
    """
    low_t = (title or "").lower()
    low_n = (name or "").lower().strip()
    # Checked FIRST: a search page carries the requested name in its title
    # ("zzz - Search - Microsoft Edge"), so name-matching before this would
    # call a failed launch a success.
    browsers = ("edge", "chrome", "firefox", "brave", "opera", "safari")
    if "search" in low_t and any(b in low_t for b in browsers):
        return "search"
    if low_n and low_n in low_t:
        return "ok"
    words = [w for w in low_n.replace(".exe", "").split() if len(w) > 2]
    if words and all(w in low_t for w in words):
        return "ok"
    return "other"


def _launch_windows_legacy(app_name: str) -> bool:
    import os
    """Peluncur lama (dipertahankan untuk aplikasi di luar kamus bila
    _settings.allow_start_menu_search = true). TIDAK dipakai secara bawaan:
    jalur Start-menu-nya menekan Enter pada hasil teratas apa pun."""

    # The absolute path of the RIGHT executable, not whatever `shell=True`
    # happens to find first (see _pick_executable).
    exe = _pick_executable(app_name) or _pick_executable(app_name.split(".")[0])
    if exe:
        try:
            subprocess.Popen(
                [exe],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name:
        try:
            # GrokBot: dulu `start {app_name}` dengan shell=True — nama dari
            # model seperti "x: & calc" menjadi perintah cmd (injeksi).
            os.startfile(app_name)               # type: ignore[attr-defined]
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _launch_macos(app_name: str) -> bool:

    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


_LINUX_TERMINAL_FALLBACKS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "xterm", "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
]

def _launch_linux(app_name: str) -> bool:

    # terminal emulators: try common ones in order
    if app_name in ("x-terminal-emulator", "gnome-terminal", "terminal"):
        for term in _LINUX_TERMINAL_FALLBACKS:
            if shutil.which(term):
                try:
                    subprocess.Popen([term], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.0)
                    return True
                except Exception:
                    continue

    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-")) or
        shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        subprocess.run(
            ["xdg-open", app_name],
            capture_output=True, timeout=5
        )
        return True
    except Exception:
        pass

    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


_OS_LAUNCHERS = {
    "Windows": lambda n: _launch_windows(n),   # didefinisikan di bawah (kamus)
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

# ═════════════════════════════════════════════════════════════════════════════
#  GrokBot 22 Okt — KAMUS APLIKASI + VERIFIKASI (Windows)
# ═════════════════════════════════════════════════════════════════════════════
# Masalah yang diperbaiki: "buka OpenCode" membuka Cursor/VS Code lalu
# dilaporkan berhasil. Penyebab berantai: (1) tebakan nama (substring/kata),
# (2) kandidat PATH tanpa ekstensi dicoba pertama, (3) Start-menu fallback
# mengetik nama lalu menekan Enter pada hasil teratas APA PUN, (4) jendela
# baru apa pun dianggap bukti, (5) /open Telegram menimpa putusan open_app.
#
# Sekarang: nama dicocokkan PERSIS dengan alias di config/apps.json; tiap
# aplikasi punya cara luncur sendiri; keberhasilan hanya dilaporkan bila
# proses ATAU jendela yang cocok dengan entri itu benar-benar muncul.
# Nama di luar kamus TIDAK ditebak — pengguna ditanya.

import json as _json
import os as _os
import re as _re
from pathlib import Path as _Path

_APPS_PATH = _Path(__file__).resolve().parent.parent / "config" / "apps.json"
_apps_cache: list = [None, 0.0]


def _key(text: str) -> str:
    return " ".join(_re.sub(r"[^\w+#.\- ]+", " ", (text or "").lower()).split())


def load_apps() -> dict:
    """config/apps.json → {"settings": {...}, "apps": {...}}; rusak → kosong."""
    try:
        mtime = _APPS_PATH.stat().st_mtime
    except OSError:
        return {"settings": {}, "apps": {}}
    if _apps_cache[0] is not None and _apps_cache[1] == mtime:
        return _apps_cache[0]
    try:
        raw = _json.loads(_APPS_PATH.read_text(encoding="utf-8"))
        data = {"settings": dict(raw.get("_settings") or {}),
                "apps": {k: v for k, v in (raw.get("apps") or {}).items()
                         if isinstance(v, dict)}}
    except Exception as e:                               # noqa: BLE001
        print(f"[open_app] config/apps.json tidak terbaca: {e}")
        data = {"settings": {}, "apps": {}}
    _apps_cache[0], _apps_cache[1] = data, mtime
    return data


def lookup_app(name: str) -> tuple[str, dict] | None:
    """(id, entri) bila `name` PERSIS sama dengan id/alias; selain itu None.

    Sengaja tanpa substring dan tanpa subset kata: "opencode" bukan "code",
    "open code please" bukan apa-apa (model diminta mengirim nama saja)."""
    k = _key(name)
    if not k:
        return None
    k2 = k[:-4] if k.endswith(".exe") else k
    for app_id, entry in load_apps()["apps"].items():
        names = {_key(app_id)} | {_key(a) for a in entry.get("aliases") or []}
        if k in names or k2 in names:
            return app_id, entry
    return None


def _expand(path: str) -> str:
    return _os.path.expandvars(_os.path.expanduser(path or ""))


def _app_paths_registry(name: str) -> str:
    """App Paths di registry (winword.exe, excel.exe, …) — nama PERSIS."""
    if _os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    sub = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, sub) as k:
                val, _ = winreg.QueryValueEx(k, "")
                val = _expand(str(val).strip('"'))
                if val and _os.path.isfile(val):
                    return val
        except OSError:
            continue
    return ""


def _resolve_cmd(name: str, exclude=()) -> str:
    """Program bernama PERSIS `name` di PATH / App Paths, melewati path yang
    memuat kata di `exclude` (mis. shim `code.cmd` milik Cursor)."""
    found = []
    first = _pick_executable(name)
    if first:
        found.append(first)
    if _os.name == "nt":
        import shutil as _sh
        for ext in (".exe", ".cmd", ".bat", ".com"):
            w = _sh.which(name + ext) if not name.lower().endswith(ext) else _sh.which(name)
            if w and w not in found:
                found.append(w)
    reg = _app_paths_registry(name)
    if reg:
        found.append(reg)
    for p in found:
        low = p.lower()
        if not any(str(x).lower() in low for x in exclude or ()):
            return p
    return ""


_CREATE_NEW_CONSOLE = 0x00000010
_CREATE_NO_WINDOW = 0x08000000


def _popen(argv: list, *, new_console: bool = False, no_window: bool = False):
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "stdin": subprocess.DEVNULL, "close_fds": True}
    if _os.name == "nt":
        flags = 0
        if new_console:
            flags |= _CREATE_NEW_CONSOLE
        elif no_window:
            flags |= _CREATE_NO_WINDOW
        if flags:
            kw["creationflags"] = flags
        if new_console:
            kw.pop("stdout"); kw.pop("stderr"); kw.pop("stdin")
    return subprocess.Popen(argv, **kw)           # shell=False — tanpa injeksi


def _try_launch(step: dict, display: str) -> str:
    """Jalankan satu langkah 'launch'. '' bila tidak bisa (tidak terpasang)."""
    kind = str(step.get("kind") or "").lower()
    args = [str(a) for a in step.get("args") or []]
    if kind == "exe":
        path = _expand(step.get("path", ""))
        if path and _os.path.isfile(path):
            _popen([path] + args)
            return path
        return ""
    if kind == "cmd":
        path = _resolve_cmd(str(step.get("name") or ""),
                            step.get("exclude_path_words") or ())
        if not path:
            return ""
        is_script = path.lower().endswith((".cmd", ".bat"))
        if is_script:
            # .cmd/.bat lewat cmd.exe /c TANPA shell=True; argumen tetap list.
            _popen(["cmd.exe", "/d", "/c", path] + args,
                   new_console=bool(step.get("new_console")),
                   no_window=not step.get("new_console"))
        else:
            _popen([path] + args, new_console=bool(step.get("new_console")))
        return path
    if kind == "terminal":
        # Program CLI/TUI (OpenCode) butuh jendela terminal SENDIRI; dulu ia
        # dijalankan tanpa konsol dengan stdout dibuang, jadi tak ada jendela.
        path = (_expand(step.get("path", "")) if step.get("path")
                else _resolve_cmd(str(step.get("name") or "")))
        if not path or not _os.path.isfile(path):
            return ""
        wt = _resolve_cmd("wt") if _os.name == "nt" else ""
        if wt:
            _popen([wt, "-w", "new", "--title", display, "cmd.exe", "/k", path] + args)
        else:
            _popen(["cmd.exe", "/k", path] + args, new_console=True)
        return path
    if kind == "uri":
        uri = str(step.get("uri") or "")
        if uri and hasattr(_os, "startfile"):
            _os.startfile(uri)                   # type: ignore[attr-defined]
            return uri
        return ""
    if kind == "appid":
        aid = str(step.get("id") or "")
        if aid and _os.name == "nt":
            _popen(["explorer.exe", "shell:AppsFolder\\" + aid])
            return "shell:AppsFolder\\" + aid
        return ""
    return ""


def _process_snapshot() -> dict:
    """{pid: name_lower}; kosong bila psutil tidak ada."""
    if not _PSUTIL:
        return {}
    out = {}
    try:
        for p in psutil.process_iter(["pid", "name"]):
            out[p.info["pid"]] = str(p.info.get("name") or "").lower()
    except Exception:                                    # noqa: BLE001
        pass
    return out


def _title_ok(entry: dict, title: str) -> bool:
    low = (title or "").lower()
    if any(str(w).lower() in low for w in entry.get("not_title_words") or ()):
        return False
    words = [str(w).lower() for w in entry.get("title_words") or () if w]
    return bool(words) and any(w in low for w in words)


def _other_app(new_names: set, new_titles: set, me: str) -> str:
    """Nama tampilan aplikasi LAIN dari kamus yang ternyata muncul."""
    for app_id, entry in load_apps()["apps"].items():
        if app_id == me:
            continue
        procs = {str(n).lower() for n in entry.get("process_names") or ()}
        if procs & new_names:
            return str(entry.get("display") or app_id)
        if any(_title_ok(entry, t) for t in new_titles):
            return str(entry.get("display") or app_id)
    return ""


def verify_launch(app_id: str, entry: dict, before_procs: dict,
                  before_titles: set, seconds: float) -> tuple[str, str]:
    """('ok'|'running'|'wrong'|'none', bukti). Menunggu maks `seconds`."""
    procs = {str(n).lower() for n in entry.get("process_names") or ()}
    deadline = time.monotonic() + max(1.0, seconds)
    while True:
        now_procs = _process_snapshot()
        new_names = {n for pid, n in now_procs.items() if pid not in before_procs}
        titles = _window_titles()
        new_titles = titles - before_titles
        hit = sorted(procs & new_names)
        if hit:
            win = next((t for t in sorted(new_titles) if _title_ok(entry, t)), "")
            return "ok", f"process {hit[0]}" + (f", window: {win}" if win else "")
        win = next((t for t in sorted(new_titles) if _title_ok(entry, t)), "")
        if win:
            return "ok", f"window: {win}"
        if entry.get("any_new_window") and new_titles:
            return "ok", f"window: {sorted(new_titles)[0]}"
        other = _other_app(new_names, new_titles, app_id)
        if other:
            return "wrong", other
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    # Sudah berjalan sebelumnya? Aplikasi satu-instans hanya memfokuskan
    # jendela lamanya — itu tetap aplikasi yang BENAR.
    if procs & set(before_procs.values()):
        win = next((t for t in sorted(_window_titles()) if _title_ok(entry, t)), "")
        return "running", (f"window: {win}" if win else "process already running")
    return "none", ""


def _launch_from_dictionary(app_id: str, entry: dict, asked: str) -> str:
    display = str(entry.get("display") or app_id)
    settings = load_apps()["settings"]
    before_procs = _process_snapshot()
    before_titles = _window_titles()
    used = ""
    errors = []
    for step in entry.get("launch") or []:
        try:
            used = _try_launch(step, display)
        except Exception as e:                           # noqa: BLE001
            errors.append(f"{step.get('kind')}: {e}")
            used = ""
        if used:
            break
    if not used:
        why = f" ({'; '.join(errors)[:160]})" if errors else ""
        return _verdict(
            f"Could not open {display}: it does not seem to be installed at "
            f"any path listed for it in config/apps.json{why}. Ask the user "
            f"where it is installed, or whether they meant another app.",
            False, reason="not_installed")
    if not before_procs and not before_titles:
        return _verdict(
            f"Started {display} ({used}) but could not verify it — psutil "
            f"and pygetwindow are unavailable. Do not claim it is open; ask "
            f"the user to confirm.",
            None, reason="no_verifier", launcher=used)
    verdict, proof = verify_launch(app_id, entry, before_procs, before_titles,
                                   float(settings.get("verify_seconds") or 8))
    if verdict == "ok":
        return _verdict(f"Opened {display} — verified: {proof}.", True, proof=proof)
    if verdict == "running":
        return _verdict(f"Opened {display} — it was already running ({proof}).",
                        True, proof=proof, already_running=True)
    if verdict == "wrong":
        return _verdict(
            f"A different app opened: {proof}, not {display}. {display} "
            f"was NOT opened. Tell the user exactly this.",
            False, reason="wrong_app", proof=proof)
    return _verdict(
        f"Launched {display} ({used}) but no {display} process or window "
        f"appeared within {int(float(settings.get('verify_seconds') or 8))} "
        f"seconds. It may still be loading, or it failed to start — do not "
        f"say it is open.",
        None, reason="no_window_after_launch", launcher=used)


def _launch_windows(app_name: str) -> bool:
    """Kompatibel dengan peluncur lama (dipakai sonde/kode lain): True bila
    nama ada di kamus dan salah satu langkah luncur berjalan."""
    found = lookup_app(app_name)
    if not found:
        if load_apps()["settings"].get("allow_start_menu_search"):
            return _launch_windows_legacy(app_name)
        return False
    for step in found[1].get("launch") or []:
        try:
            if _try_launch(step, str(found[1].get("display") or found[0])):
                return True
        except Exception:                                # noqa: BLE001
            continue
    return False


def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return _verdict("No application name provided.", False, reason="no_name")

    if player:
        player.write_log(f"[open_app] {app_name}")

    if _SYSTEM == "Windows":
        found = lookup_app(app_name)
        print(f"[open_app] '{app_name}' → {found[0] if found else 'tidak ada di kamus'}")
        try:
            if found:
                return _launch_from_dictionary(found[0], found[1], app_name)
            # Di luar kamus: hanya program yang namanya PERSIS sama.
            exact = _resolve_cmd(app_name) if _re.fullmatch(r"[\w.\-]+", app_name) else ""
            if exact:
                entry = {"display": app_name,
                         "process_names": [_os.path.basename(exact).lower()],
                         "title_words": [app_name.lower()],
                         "launch": [{"kind": "exe", "path": exact}]}
                return _launch_from_dictionary(app_name.lower(), entry, app_name)
            if load_apps()["settings"].get("allow_start_menu_search"):
                ok = _launch_windows_legacy(app_name)
                return (_verdict(f"Ran the Start-menu search for {app_name} (unverified).",
                                 None, reason="start_menu_unverified")
                        if ok else
                        _verdict(f"Could not open {app_name}.", False,
                                 reason="start_menu_failed"))
            known = ", ".join(sorted(str(e.get("display") or k) for k, e in
                                     load_apps()["apps"].items())[:40])
            return _verdict(
                f"Unknown app: '{app_name}' is not in config/apps.json and no "
                f"program with exactly that name is installed. Nothing was "
                f"opened. Ask the user which app they mean. Known apps: {known}.",
                False, reason="unknown_app")
        except Exception as e:                           # noqa: BLE001
            print(f"[open_app] Error: {e}")
            return _verdict(f"Failed to open {app_name}: {e}", False, reason="exception")

    # ── macOS / Linux: perilaku lama (kamus di atas khusus Windows) ──────────
    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return _verdict(f"Unsupported operating system: {_SYSTEM}", False,
                        reason="unsupported_os")

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    try:
        before = _window_titles()
        launched = False
        if launcher(normalized):
            launched = True
        elif normalized.lower() != app_name.lower() and launcher(app_name):
            launched = True

        if launched:
            if before:
                title = _wait_new_window(before)
                if not title:
                    return _verdict(
                        f"Ran the launcher for {app_name} but no new window "
                        f"appeared — it may still be loading, or the name "
                        f"may not match anything installed.",
                        None, reason="no_window_after_launch")
                verdict = _judge_window(app_name, title)
                if verdict == "ok":
                    return _verdict(f"Opened {app_name} — window: {title}.",
                                    True, proof=f"window: {title}")
                if verdict == "search":
                    return _verdict(
                        f"Nothing called {app_name} was found — a web "
                        f"search opened instead ({title}).",
                        False, reason="search_instead_of_app")
                return _verdict(
                    f"A window appeared, but it is not {app_name} — "
                    f"window: {title}. Check whether that is the app you "
                    f"meant.",
                    False, reason="wrong_window")
            return _verdict(f"Ran the launcher for {app_name} (unverified).",
                            None, reason="launcher_no_window_check")
        return _verdict(
            f"Could not confirm that {app_name} launched. "
            f"It may still be loading, or it might not be installed.",
            None, reason="launch_unconfirmed")
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return _verdict(f"Failed to open {app_name}: {e}", False, reason="exception")



# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "open_app",
    "description": "Opens an application on the computer by its exact name (OpenCode, Cursor, VS Code, WorkBuddy, WhatsApp, Telegram, Chrome, …; the list lives in config/apps.json). Names are matched exactly — never guessed. Only say the app is open when the result starts with 'Opened'; otherwise tell the user what actually happened, or ask which app they meant.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "Exact name of the application only, as the user said it (e.g. 'OpenCode', 'Cursor', 'WhatsApp') — no extra words"
            }
        },
        "required": [
            "app_name"
        ]
    },
    "handler": open_app,
}
