"""
plugins/_telegram_ops.py — the extra things telegram_remote.py can put on a phone.

WHY THIS FILE EXISTS AT ALL
    telegram_remote.py is a security boundary: an allowlist, two replay walls, a
    rate limiter, a pairing budget and a reply window. That file wants to stay
    small enough that somebody can read all of it before trusting it with their
    computer. Screen capture and hardware telemetry are not security; they are
    payload. They live here so the boundary does not grow every time a feature
    is added.

    The leading underscore is load-bearing: core/plugin_loader.py skips `_*.py`,
    so this is never treated as a plugin of its own and costs the model NOTHING
    — no second tool declaration, no extra characters on every connection. It is
    the same arrangement _google_core.py has with the Gmail/Calendar pair.

    Delete this file and the bridge still runs; it simply offers fewer things,
    and says so in a sentence.

WHAT AN "OP" IS
    One capability, declared once, in English, as data:

        action  the value the MODEL writes into its JSON when the user asks for
                this in whatever language they happen to speak
        cmd     a slash token for the phone — a shortcut that skips the model
        desc    one English line, which becomes both the model's schema text and
                the description Telegram shows in the bot's command menu
        where   who may trigger it: the desk, the phone, or both
        gate    a settings toggle that must be ON, or None
        arg     a hint for the one optional argument, or None
        button  whether it earns a place on the phone's thumb keyboard. Anything
                that cannot be taken back should not sit next to something that
                is pressed daily, so this is not simply "is it useful".
        confirm whether it has to be sent twice. The second press is the
                confirmation, which needs no word in any language - see
                preview() below.

    telegram_remote.py merges this tuple with its own and generates the tool
    schema, the Telegram command menu, the phone keyboard and /help from the
    result. Nothing is written down twice, and no natural-language keyword — in
    any language — appears anywhere in the dispatch path.

EVERYTHING HEAVY IS IMPORTED INSIDE A FUNCTION
    Plugin discovery executes telegram_remote.py on every launch, and it imports
    this module. actions/screen_processor.py pulls in numpy and probes for cv2 at
    module scope; mss and Pillow are not free either. None of that may be charged
    to a boot where nobody opens the remote, so this module's own imports are all
    deferred to the moment a picture is actually asked for.
"""
from __future__ import annotations

# The picture is meant to be READ on a phone — a window title, an error dialog,
# a progress bar. actions/screen_processor.py targets 1280x720 because that is
# what a vision model needs, and on a 4K desktop that is exactly the size where
# text stops being legible. This is a different consumer, so it gets its own
# numbers rather than borrowing ones tuned for something else.
_MAX_W, _MAX_H, _QUALITY = 1920, 1080, 80

# sendPhoto refuses anything over 10 MB. Only reachable when Pillow is missing
# and a raw PNG would have to go up unmodified.
_PHOTO_LIMIT = 9_500_000

# sendDocument's own ceiling for a bot.
_DOC_LIMIT = 45_000_000

# Never synthesise more than this in one go: a long answer read aloud is a
# phone full of minutes, and the bridge already sends the text beside it.
_SPEAK_CAP = 700


EXTRA_OPS = (
    {
        "action": "screenshot",
        "cmd":    "screen",
        "icon":   "\U0001F4F8",
        "desc":   ("capture this computer's screen right now and send the picture "
                   "to the phone"),
        "where":  "both",
        "button": True,
        "gate":   "allow_screenshot",
        "arg":    "a monitor number, or 'all' for every monitor at once",
    },
    {
        "action": "system_status",
        "cmd":    "sys",
        "icon":   "\U0001F4CA",
        "desc":   ("send this computer's CPU, memory, GPU, temperature and uptime "
                   "to the phone"),
        "where":  "both",
        "button": True,
        "gate":   None,
        "arg":    None,
    },
    {
        "action": "camera",
        "cmd":    "cam",
        "icon":   "\U0001F4F7",
        "desc":   ("take one picture with this computer's webcam and send it to "
                   "the phone"),
        "where":  "both",
        "button": False,
        "gate":   "allow_camera",
        "arg":    None,
    },
    {
        "action": "get_file",
        "cmd":    "get",
        "icon":   "\U0001F4CE",
        "desc":   ("send a file out of the shared folder, or list what is in it"),
        "where":  "remote",
        "button": False,
        # Not a switch but a folder name: the gate opens by naming ONE place,
        # which is also the only place this can ever read from.
        "gate":   "share_folder",
        "arg":    "a file name, or nothing to list the folder",
    },
    {
        "action": "pending",
        "cmd":    "pending",
        "icon":   "⚠",
        "desc":   ("say whether a confirmation is waiting to be pressed on the "
                   "computer's screen"),
        "where":  "remote",
        "button": True,
        "gate":   None,
        "arg":    None,
    },
    {
        "action": "undo",
        "cmd":    "undo",
        "icon":   "↩",
        "desc":   ("take back the last thing the assistant did to a file or a "
                   "setting"),
        "where":  "remote",
        "button": False,
        "gate":   None,
        "arg":    None,
        "confirm": True,
    },
    # The two things a phone actually needs to OPERATE the machine, not just
    # look at it. Without a fast path these only work by talking to the model,
    # and the model can decline or pick the wrong tool — which is exactly the
    # failure this pair removes: /open and /click are protocol tokens, so no
    # model sits between the press and the action.
    {
        "action": "open_app",
        "cmd":    "open",
        "icon":   "\U0001F680",
        "desc":   "open an application on this computer by name",
        "where":  "both",
        "button": True,
        "gate":   None,
        "arg":    "the application name, e.g. notepad",
    },
    {
        "action": "click",
        "cmd":    "click",
        "icon":   "\U0001F5B1",
        "desc":   ("find an element on this computer's screen from a description "
                   "and click it"),
        "where":  "both",
        "button": True,
        "gate":   None,
        "arg":    "what to click, e.g. 'tombol Coba Lagi'",
        # Single press on purpose: the description is typed by the person who
        # wants the click, so the intent is already explicit — and a two-press
        # confirmation made the command fail more often than it made it safe
        # (the window closed while somebody was still typing). The screenshot
        # that comes back is the record of what was clicked.
    },
    {
        "action": "watch",
        "cmd":    "watch",
        "icon":   "\U0001F39E",
        "desc":   ("send a short burst of screenshots, one a second, so the phone "
                   "can see what the screen is doing"),
        "where":  "both",
        "button": True,
        "gate":   "allow_screenshot",
        "arg":    "seconds to watch (default 6, max 10)",
    },
)


# -- screen -------------------------------------------------------------------

def _grab(arg: str) -> tuple[bytes, int, int, int, int]:
    """Return (png, width, height, monitor_index, monitor_count).

    mss numbers monitors from 1 and keeps the union of all of them at index 0,
    which is exactly the two things a remote user wants: "the screen" and "all
    of it". A machine with one display has no index 1 at all, so the default is
    chosen from the list rather than assumed.
    """
    try:
        import mss
        import mss.tools
    except ImportError:
        raise RuntimeError(
            "Screen capture needs the 'mss' package - run: pip install mss"
        ) from None

    want = (arg or "").strip().lower()
    try:
        with mss.mss() as sct:
            mons = list(sct.monitors)
            if not mons:
                raise RuntimeError("The graphics driver reported no screen at all.")
            count = max(0, len(mons) - 1)
            if want in ("all", "*", "0"):
                idx = 0
            elif want.isdigit():
                idx = int(want)
                if idx >= len(mons):
                    raise RuntimeError(
                        f"There is no screen {idx} - this computer reports {count}."
                    )
            else:
                idx = 1 if len(mons) > 1 else 0
            shot   = sct.grab(mons[idx])
            width  = shot.width
            height = shot.height
            png    = mss.tools.to_png(shot.rgb, shot.size)
    except RuntimeError:
        raise
    except Exception as e:
        # Capture is the one thing here that can fail for reasons outside this
        # program: a locked session, a screen-recording permission that was
        # never granted, a compositor that refuses to be read. Naming the cause
        # is all this layer can honestly do about any of them.
        raise RuntimeError(f"The screen could not be captured ({e}).") from None

    return png, width, height, idx, count


def _compress(png: bytes, max_w: int = _MAX_W, max_h: int = _MAX_H) -> tuple[bytes, str, str]:
    """PNG to JPEG, and say plainly when it could not be done.

    The size is a parameter because a burst of frames and a single screenshot
    have different budgets: one frame may be 1920 wide, ten of them should be
    smaller or the album takes half a minute to upload.
    """
    try:
        import io

        import PIL.Image
    except ImportError:
        if len(png) > _PHOTO_LIMIT:
            raise RuntimeError(
                "The screenshot is too large to send uncompressed - run: "
                "pip install pillow"
            ) from None
        return png, "image/png", "png"

    img = PIL.Image.open(io.BytesIO(png)).convert("RGB")
    img.thumbnail((max_w, max_h), PIL.Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_QUALITY, optimize=False)
    return buf.getvalue(), "image/jpeg", "jpg"


def _screenshot(arg: str = "", _cfg: dict | None = None) -> dict:
    import time as _time

    png, w, h, idx, count = _grab(arg)
    data, mime, ext = _compress(png)

    # The caption is deliberately almost all digits and symbols. Everything else
    # this bridge says is in English, because shipping a translation table would
    # be shipping a hardcoded list of languages - but a resolution and a clock
    # time read the same in every one of them.
    if idx == 0 and count > 1:
        which = "⛶ all"
    elif count > 1:
        which = f"\U0001F5B5 {idx}/{count}"
    else:
        which = "\U0001F5B5"
    caption = f"{which}  ·  {w}×{h}  ·  {_time.strftime('%H:%M:%S')}"

    return {"kind": "photo", "data": data, "mime": mime,
            "name": f"screen.{ext}", "caption": caption}


def _camera(_arg: str = "", _cfg: dict | None = None) -> dict:
    """One webcam frame.

    The bundled capture already finds the camera, warms it through ten discarded
    frames because the first one out of a cold sensor is black, and releases it
    again — so a second copy of that here would be a second thing to get wrong
    on a laptop with three video devices.
    """
    import time as _time

    from actions.screen_processor import _capture_camera
    try:
        data, mime = _capture_camera()
    except Exception as e:
        raise RuntimeError(f"The camera could not be used ({e}).") from None
    return {"kind": "photo", "data": data, "mime": mime, "name": "camera.jpg",
            "caption": f"\U0001F4F7  ·  {_time.strftime('%H:%M:%S')}"}


# -- files, in and out --------------------------------------------------------
#
# Both directions are shut until a FOLDER IS NAMED at the desk, and each names
# its own. That is the gate: not a switch that means "files, generally", but a
# single directory that is the only place this can write to, and a single
# directory that is the only place it can read from. Nothing from Telegram can
# set either of them.

def _safe_name(raw: str) -> str:
    """A name Telegram supplied is not a name this machine has to accept.

    Everything but the final component is dropped — on both separators, because
    a name built on one operating system arrives unchanged on another — and what
    is left cannot be empty, cannot be a dot, and cannot climb.
    """
    name = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*')
    name = name.strip(". ")
    return name[:120] or "file"


def _folder(cfg: dict | None, key: str):
    from pathlib import Path
    raw = str((cfg or {}).get(key) or "").strip()
    if not raw:
        raise RuntimeError("No folder is set for that at the computer.")
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise RuntimeError(f"'{raw}' is not a folder on this computer.")
    return path.resolve()


def save_incoming(name: str, data: bytes, cfg: dict) -> str:
    """Write a file the phone sent into the inbox folder. Returns its path."""
    folder = _folder(cfg, "inbox_folder")
    target = folder / _safe_name(name)
    # Never overwrite. Two photos taken a minute apart both arrive as
    # "photo.jpg", and losing the first to the second would be silent.
    if target.exists():
        stem, dot, ext = target.name.partition(".")
        n = 2
        while target.exists():
            target = folder / f"{stem}-{n}{dot}{ext}"
            n += 1
    target.write_bytes(data)
    return str(target)


def _get_file(arg: str = "", cfg: dict | None = None) -> dict:
    """Send one file out of the shared folder, or say what is in it."""
    folder = _folder(cfg, "share_folder")
    wanted = _safe_name(arg)

    if not str(arg or "").strip():
        names = sorted(p.name for p in folder.iterdir() if p.is_file())
        if not names:
            return {"kind": "text", "text": "The shared folder is empty."}
        listing = "\n".join(f"• {n}" for n in names[:60])
        more = f"\n… and {len(names) - 60} more" if len(names) > 60 else ""
        return {"kind": "text",
                "text": f"{folder.name}:\n{listing}{more}\n\nSend /get <name>."}

    target = (folder / wanted).resolve()
    # Belt and braces behind _safe_name: whatever the name turned out to be, the
    # file that is about to be read must sit directly in the named folder. A
    # symlink pointing out of it resolves here and fails this.
    if target.parent != folder or not target.is_file():
        raise RuntimeError(f"There is no '{wanted}' in the shared folder.")
    size = target.stat().st_size
    if size > _DOC_LIMIT:
        raise RuntimeError(f"'{target.name}' is {size / 1e6:.0f} MB — Telegram "
                           f"will not carry more than {_DOC_LIMIT // 1_000_000} MB.")
    return {"kind": "document", "data": target.read_bytes(), "name": target.name,
            "mime": "application/octet-stream",
            "caption": f"{target.name}  ·  {size / 1000:.0f} KB"}


# -- hardware -----------------------------------------------------------------

def _system_card(_arg: str = "", _cfg: dict | None = None) -> dict:
    """The bundled system_monitor already knows how to read this machine - GPU
    through NVML, temperature through whatever the platform exposes, and None
    where the platform exposes nothing. Re-implementing any of that here would
    be a second, worse copy that drifts away from the first one."""
    try:
        from actions.system_monitor import get_system_status
    except Exception as e:
        raise RuntimeError(f"The hardware readout is unavailable ({e}).") from None

    try:
        s = get_system_status() or {}
    except Exception as e:
        raise RuntimeError(f"The hardware readout failed ({e}).") from None

    rows = [f"\U0001F5A5  CPU   {float(s.get('cpu_percent') or 0):.0f}%"]

    ram = s.get("ram_percent")
    if ram is not None:
        used, total = s.get("ram_used_gb"), s.get("ram_total_gb")
        detail = f"  ({used}/{total} GB)" if used is not None and total else ""
        rows.append(f"\U0001F9E0  RAM   {float(ram):.0f}%{detail}")

    # A value the platform could not report is left out rather than shown as 0.
    # A machine with no discrete GPU reporting "GPU 0%" is a lie that looks like
    # a reading.
    gpu = s.get("gpu_percent")
    if gpu is not None:
        rows.append(f"\U0001F3AE  GPU   {float(gpu):.0f}%")

    temp = s.get("cpu_temp_c")
    if temp is not None:
        rows.append(f"\U0001F321  TEMP  {float(temp):.0f}°C")

    rows.append(f"⏱  UP    {s.get('uptime', '?')}  ·  "
                f"{s.get('process_count', '?')} proc")
    return {"kind": "text", "text": "\n".join(rows)}


# -- the confirmation gate, seen from a phone ---------------------------------

def confirm_pending() -> str:
    """The title of whatever is waiting on the HUD, or ''.

    Read as structured state rather than scraped out of the activity log. The
    log lines core writes around this ("Awaiting confirmation — ...", "Cancelled
    — ...") are English sentences, and matching them would put a table of
    English words on the path of a feature whose whole point is that it works in
    every language. One function call carries the same fact and cannot drift.
    """
    try:
        from core.confirm import pending_title
        return pending_title() or ""
    except Exception:
        return ""


def _pending(_arg: str = "", _cfg: dict | None = None) -> dict:
    title = confirm_pending()
    if not title:
        return {"kind": "text",
                "text": "Nothing is waiting for a hand at the computer."}
    return {"kind": "text",
            "text": (f"⚠ Waiting for someone at the computer to press "
                     f"CONFIRM:\n\n{title}\n\nI cannot press it from here, and "
                     f"that is deliberate — an irreversible action needs a "
                     f"hand in the room.")}


# -- undo ---------------------------------------------------------------------

def _undo(_arg: str = "", _cfg: dict | None = None) -> dict:
    from core.undo import undo_last
    return {"kind": "text", "text": undo_last()}


def _undo_preview(_arg: str = "") -> str:
    from core.undo import peek
    what = peek()
    if not what:
        raise RuntimeError("There is nothing to undo. I only track what I "
                           "changed myself — files I moved or wrote, and "
                           "settings I adjusted.")
    return what


# -- operating the machine from the phone -------------------------------------

def _proof(said: str, cfg: dict | None, pause: float = 0.8) -> dict:
    """The sentence an action produced, with a screenshot behind it.

    A remote action that reports success and shows nothing is a claim, not a
    result. When screen sharing is allowed the picture is the proof; when it is
    not, the sentence is still the answer.
    """
    import time as _time

    if (cfg or {}).get("allow_screenshot"):
        try:
            _time.sleep(pause)      # the window needs a moment to appear
            shot = _screenshot("", cfg)
            shot["caption"] = f"{str(said)[:180]}  ·  {_time.strftime('%H:%M:%S')}"
            return shot
        except Exception:
            pass
    return {"kind": "text", "text": said}


def _windows_now() -> set[str]:
    """Titles of the windows currently on screen, or an empty set if unknown."""
    try:
        import pygetwindow
        return {t for t in pygetwindow.getAllTitles() if t and t.strip()}
    except Exception:
        return set()


def _open(arg: str = "", cfg: dict | None = None) -> dict:
    """Launch an application by name — the fast path behind /open.

    Lewat pintu keputusan pusat (core/decision_loop.dispatch): izin → open_app
    → verifikasi → balasan. open_app sendiri yang membuktikan aplikasi yang
    BENAR terbuka (proses/jendela dicocokkan dengan config/apps.json). Versi
    lama membandingkan judul jendela di sini lalu MENIMPA putusan open_app
    dengan "Opened <nama>" untuk jendela baru APA PUN — itulah sebabnya
    Cursor yang terbuka dilaporkan sebagai OpenCode.
    """
    name = (arg or "").strip()
    if not name:
        raise RuntimeError("Say which application to open — /open notepad.")
    try:
        from actions.open_app import open_app
        from core import decision_loop as _decision
    except Exception as e:                               # noqa: BLE001
        raise RuntimeError(f"The app launcher is unavailable ({e}).") from None

    out = _decision.dispatch(
        "open_app", {"app_name": name},
        lambda _t, a: open_app(a) or "",
        source="telegram", explicit=True)
    said = (out.get("reply") or "").strip() or f"Could not open {name}."
    if out.get("verified") is False and not said.startswith("\u26a0"):
        said = "\u26a0 " + said
    return _proof(said, cfg, pause=0.6)


def _click(arg: str = "", cfg: dict | None = None) -> dict:
    """Find an element by description and click it — the fast path behind /click."""
    what = (arg or "").strip()
    if not what:
        raise RuntimeError("Say what to click — /click tombol Coba Lagi.")
    try:
        from actions.computer_control import computer_control
        from core import decision_loop as _decision
    except Exception as e:                               # noqa: BLE001
        raise RuntimeError(f"Computer control is unavailable ({e}).") from None
    out = _decision.dispatch(
        "computer_control", {"action": "screen_click", "description": what},
        lambda _t, a: computer_control(a) or "",
        source="telegram", explicit=True)
    said = (out.get("reply") or "").strip()
    if "not found" in said.lower():
        # Say what to do next, not just what failed: the screen may have
        # changed, or the description may be a paraphrase of the button's text.
        said += (" — send /screen to see what I see, then describe the element "
                 "with the words written on it.")
    return _proof(said or f"Clicked: {what}", cfg, pause=0.6)


def _click_preview(arg: str = "") -> str:
    what = (arg or "").strip()
    return (f"click \u201c{what}\u201d on the computer screen" if what
            else "click an element on the computer screen")


def _watch(arg: str = "", cfg: dict | None = None) -> dict:
    """A burst of frames, one a second — the closest a bot can get to live.

    Telegram has no live video for bots: sendMediaGroup takes an album of still
    pictures, so "live" here means a short sequence the phone can scroll through
    and see the screen change. Frames are smaller than a single screenshot on
    purpose: ten of them must still upload in a few seconds.
    """
    import re as _re
    import time as _time

    m = _re.search(r"\d+", str(arg or ""))
    seconds = max(2, min(10, int(m.group()) if m else 6))

    frames: list[tuple[bytes, str]] = []
    for i in range(seconds):
        png, _w, _h, _idx, _count = _grab("")
        data, mime, _ext = _compress(png, 1280, 720)
        frames.append((data, mime))
        if i + 1 < seconds:
            _time.sleep(1.0)

    return {"kind": "album", "frames": frames, "name": "frame",
            "caption": f"\U0001F39E {seconds}s  ·  {_time.strftime('%H:%M:%S')}"}


def album_payload(job: dict) -> tuple[dict, dict]:
    """The sendMediaGroup payload for an album job.

    Built here rather than in the bridge so the security boundary stays small —
    and so the shape can be tested without a token or a network.
    """
    import json

    frames = list(job.get("frames") or [])[:10]     # Telegram's album ceiling
    media: list[dict] = []
    files: dict[str, tuple] = {}
    for i, frame in enumerate(frames):
        data, mime = (frame if isinstance(frame, (tuple, list))
                      else (frame, "image/jpeg"))
        key = f"f{i}"
        item = {"type": "photo", "media": f"attach://{key}"}
        if i == 0 and job.get("caption"):
            item["caption"] = str(job["caption"])[:1024]
        media.append(item)
        files[key] = (f"{job.get('name') or 'frame'}{i}.jpg",
                      data, mime or "image/jpeg")
    return {"chat_id": job.get("chat"), "media": json.dumps(media)}, files


_PREVIEWS = {"undo": _undo_preview, "click": _click_preview}


def preview(action: str, arg: str = "") -> str:
    """What a confirm-op is ABOUT to do, so the phone can be shown it first.

    Raises RuntimeError when there is nothing to do — which is the answer, not
    an error, and is sent to the chat as it stands. The argument is carried
    through because a click's subject is the description, not the verb.
    """
    fn = _PREVIEWS.get((action or "").strip().lower())
    return fn(arg) if fn else ""


# -- alerts, for when nobody is at the desk -----------------------------------

_monitor = None


def away_alert() -> str | None:
    """One line about a machine in trouble, or None.

    main.py runs this same monitor, but it only speaks an alert while JARVIS is
    AWAKE and somebody is there to hear it — which is exactly the wrong half of
    the day for this. A machine that overheats while you are out says nothing at
    all today. A separate instance is not a duplicate: it has its own cooldown
    state, and its audience is a phone rather than a room.
    """
    global _monitor
    try:
        from actions.system_monitor import SystemMonitor
        if _monitor is None:
            _monitor = SystemMonitor()
        line = _monitor.check()
    except Exception:
        return None
    if not line:
        return None
    # "[SYSTEM_ALERT]" is a marker addressed to the model, not to a person.
    return line.replace("[SYSTEM_ALERT]", "").strip() or None


# -- voice: hearing a voice note, and answering with one ----------------------
#
# Nothing here installs anything. There are two routes for each direction and
# the choice is NOT a new setting — it follows the one the user already made in
# ENGINE & PROVIDERS. Somebody who moved to a local pipeline did that so their
# audio would stay on the machine, and a plugin that quietly posted their voice
# notes to Google would undo the whole point of it. Somebody on the default
# Gemini install has already accepted that trade for every sentence they speak
# at the microphone, so the same route is used here and nothing extra needs
# downloading.

def _slot(slot: str) -> str:
    """The provider id filling one pipeline slot, or '' when not in that mode."""
    try:
        from memory.config_manager import get_pipeline, get_provider_mode
        if get_provider_mode() == "pipeline":
            return str(get_pipeline().get(slot) or "").strip()
    except Exception:
        pass
    return ""


def _build(pid: str):
    """The provider instance the app itself would use.

    registry.build caches by provider AND config, so this normally hands back
    the very object the live engine already holds — model loaded, kernels warm,
    no second copy of a speech model in memory. The fallback builds a private
    one, because a shared instance that cannot be reached is worse than a slow
    one that can.
    """
    from memory.config_manager import get_provider_config
    from providers import registry
    cfg = get_provider_config(pid)
    try:
        return registry.build(pid, cfg)
    except Exception:
        decl = registry.get_declaration(pid) or {}
        factory = decl.get("factory")
        if not callable(factory):
            raise
        return factory(cfg)


def stt_ready() -> tuple[bool, str]:
    """Can a voice note be turned into words, and by what."""
    pid = _slot("stt")
    if pid:
        return True, pid
    try:
        from core import gemini
        return bool(gemini.api_key()), "Gemini"
    except Exception:
        return False, ""


def _decode_pcm(data: bytes, rate: int = 16_000) -> bytes:
    """A Telegram voice note is OGG/Opus; a speech model wants 16 kHz mono PCM.

    PyAV does the decoding, and it is not a new dependency: faster-whisper
    installs it, so the only route that needs it is the only route that has it.
    """
    try:
        import io

        import av
    except ImportError:
        raise RuntimeError(
            "Decoding a voice note needs PyAV, which normally arrives with "
            "faster-whisper — run: pip install av"
        ) from None

    chunks: list[bytes] = []
    with av.open(io.BytesIO(data)) as container:
        if not container.streams.audio:
            raise RuntimeError("That file has no audio in it.")
        stream = container.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=rate)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().tobytes())
        for out in resampler.resample(None):        # flush the tail
            chunks.append(out.to_ndarray().tobytes())
    return b"".join(chunks)


_spoken_language = ""


def _transcribe_local(data: bytes, pid: str) -> str:
    global _spoken_language
    import asyncio

    stt = _build(pid)
    pcm = _decode_pcm(data)
    said = (asyncio.run(stt.transcribe(pcm, 16_000)) or "").strip()
    # Whisper reports the language it heard as part of the recognition it has
    # just done, and the pipeline engine uses it to choose the voice for the
    # reply. Carrying it the same distance here costs nothing and means the
    # answer comes back in the language it was asked in — without this file
    # containing a single language name, or a detector, or a setting.
    _spoken_language = getattr(stt, "detected_language", "") or ""
    return said


def _transcribe_gemini(data: bytes, mime: str) -> str:
    from google.genai import types as gtypes

    from core import gemini
    # Through core/gemini.py rather than a client of its own: the ladder, the
    # timeout and the cooldowns are exactly what a plugin should not be
    # reinventing, and this call is no different from the nineteen others in
    # the app that go the same way.
    return gemini.text(
        [gtypes.Part.from_bytes(data=data, mime_type=mime or "audio/ogg"),
         "Transcribe this voice message word for word, in the language it is "
         "spoken in. Reply with the transcription alone — no quotation marks, "
         "no translation, no commentary, no apology. If there is nothing "
         "intelligible in it, reply with nothing at all."],
        timeout_ms=45_000, default="").strip()


def transcribe(data: bytes, mime: str = "audio/ogg") -> str:
    pid = _slot("stt")
    if pid:
        return _transcribe_local(data, pid)
    return _transcribe_gemini(data, mime)


def tts_ready() -> tuple[bool, str]:
    pid = _slot("tts")
    if pid:
        return True, pid
    # Every desktop has a voice of some kind and it needs no download. It is
    # also the weakest of them — on an ordinary Windows install it knows two
    # languages — so it is the floor, not the choice.
    return True, "system_tts"


_SPEAKER_RATE = 24_000        # the rate the app's own speaker stream uses


def _duration_s(pcm: bytes, rate: int = _SPEAKER_RATE) -> float:
    return len(pcm) / (2.0 * rate)          # 16-bit mono


def _to_wav(pcm: bytes, rate: int = _SPEAKER_RATE) -> bytes:
    """PCM to a WAV container — stdlib only, no new dependency."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _wav_seconds(data: bytes) -> float:
    try:
        import io
        import wave

        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 1.0


def _system_voice(text: str) -> bytes:
    """The desktop's own voice, as WAV bytes.

    No download and no new dependency: SAPI through pywin32, which the app
    already has on Windows. The previous version asked the provider registry
    for a "system_tts" provider — a kind the registry cannot build — and
    imported `providers.audio`, a module that does not exist, so every spoken
    reply failed with an ImportError before making a sound.
    """
    import os
    import tempfile

    try:
        import pythoncom
        import win32com.client
    except ImportError:
        raise RuntimeError(
            "No voice on this machine — configure a TTS provider in ENGINE & "
            "PROVIDERS, or install pywin32 on Windows.") from None

    path = os.path.join(tempfile.gettempdir(), f"jarvis_reply_{os.getpid()}.wav")
    pythoncom.CoInitialize()
    try:
        sp = win32com.client.Dispatch("SAPI.SpVoice")
        st = win32com.client.Dispatch("SAPI.SpFileStream")
        st.Open(path, 3)                     # 3 = SSFMCreateForWrite
        sp.AudioOutputStream = st
        sp.Speak(text)
        st.Close()
    except Exception as e:                   # noqa: BLE001
        raise RuntimeError(f"The system voice failed ({e}).") from None
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
    try:
        with open(path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass
    if not data:
        raise RuntimeError("The system voice produced no sound.")
    return data


def synthesize(text: str) -> dict:
    """Words to a job the sender can post as audio.

    Telegram shows a proper voice bubble only for OGG/Opus, which needs ffmpeg
    to produce. Where ffmpeg is absent the WAV goes up as an audio file
    instead: a bigger download and a duller bubble, but it plays with one tap,
    which is the part that matters. Nothing is installed either way.
    """
    import shutil
    import subprocess

    text = (text or "").strip()
    if not text:
        raise RuntimeError("Nothing to say.")
    if len(text) > _SPEAK_CAP:
        text = text[:_SPEAK_CAP] + "…"

    wav: bytes | None = None

    # 1) a real TTS provider, when one is configured in ENGINE & PROVIDERS
    pid = _slot("tts")
    if pid:
        try:
            import asyncio

            tts = _build(pid)

            async def _collect() -> bytes:
                out: list[bytes] = []
                # `language` is accepted by the voice that can act on it and
                # ignored by the ones that cannot.
                try:
                    stream = tts.speak(text, language=_spoken_language)
                except TypeError:
                    stream = tts.speak(text)
                async for piece in stream:
                    out.append(piece)
                return b"".join(out)

            pcm = asyncio.run(_collect())
            if pcm:
                wav = _to_wav(pcm)
        except Exception:
            wav = None                       # fall through to the desktop voice

    # 2) the desktop's own voice — the floor, and what makes this work today
    if wav is None:
        wav = _system_voice(text)

    seconds = max(1, int(_wav_seconds(wav)))

    if shutil.which("ffmpeg"):
        try:
            done = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-i", "pipe:0", "-c:a", "libopus", "-b:a", "24k",
                 "-f", "ogg", "pipe:1"],
                input=wav, capture_output=True, timeout=60)
            if done.returncode == 0 and done.stdout:
                return {"kind": "voice", "data": done.stdout, "name": "reply.ogg",
                        "mime": "audio/ogg", "seconds": seconds}
        except Exception:
            pass          # a build without libopus is not a reason to say nothing

    return {"kind": "audio", "data": wav, "name": "reply.wav",
            "mime": "audio/wav", "seconds": seconds}


# -- the one entry point telegram_remote.py uses ------------------------------

_RUNNERS = {
    "screenshot":    _screenshot,
    "system_status": _system_card,
    "camera":        _camera,
    "get_file":      _get_file,
    "pending":       _pending,
    "undo":          _undo,
    "open_app":      _open,
    "click":         _click,
    "watch":         _watch,
}


def produce(action: str, arg: str = "", cfg: dict | None = None) -> dict:
    """Run one op and hand back something the bridge's sender can post.

    Returns a job dict - {"kind": "text", ...} or {"kind": "photo", ...}. Raises
    RuntimeError carrying a sentence a non-programmer can act on; the bridge
    sends that sentence to the chat that asked, so it must never be a traceback.
    """
    fn = _RUNNERS.get((action or "").strip().lower())
    if fn is None:
        raise RuntimeError(f"'{action}' is not something this bridge can send.")
    return fn(arg or "", cfg or {})
