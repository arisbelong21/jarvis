"""
One place where the assistant's one-shot Gemini calls are made.

WHY THIS EXISTS
    The live conversation runs on the Live API and is not what this file is
    about. Everything else — reading a screenshot, parsing a flight page,
    turning a request into a shell command, working out which WhatsApp button
    answers a call — was a separate `genai.Client(...)` built at the point of
    use, with the model name written inline. Sixteen files did it, twenty-six
    times, and not one of them set a timeout.

    That is not tidiness, it is three real faults:

    NO TIMEOUT.  The SDK waits forever by default. `gemini-flash-latest` spent
    an afternoon returning 504 DEADLINE_EXCEEDED, and every one of those calls
    became an unbounded hang — measured at ten seconds of silence while a phone
    rang, and worse elsewhere, because nothing was there to give up.

    NO FALLBACK.  One hardcoded alias meant that when that alias was unwell,
    the feature was simply gone. A ladder costs nothing when the first rung
    works and saves the feature when it does not.

    NO SINGLE PLACE TO CHANGE.  A new model release meant editing sixteen files
    and hoping none were missed.

THE LIVE MODEL DOES THIS WORK, AND IT LEADS THE LADDER
    Not the user's conversation — a separate, throwaway session per call, so
    nothing a plugin asks is ever heard by the person at the microphone.

    It leads because of quota. This is a voice assistant; the Live API is the
    dependency it already has, and it draws on a different pool from the text
    models. On the free tier it is the TEXT pool that runs out, and when it does
    every side call fails and the feature behind it dies with it. Putting Live
    first means ordinary use stops spending the pool that runs dry.

    The reply arrives through output_transcription, because these models refuse
    response_modalities=["TEXT"] with a 1007 — they only speak. That sounds
    fatal for structured output and is not: the transcription is the model's own
    text of what it said, and it returned "Mum ❤ click here for contact info",
    indented Python inside markdown fences, and src/utils/helpers_v2.py
    character for character.

    It cannot carry grounding metadata, so grounded web search stays on REST.

THE LADDER, MEASURED
    Live, one throwaway session:
        connect                     0.24s
        short structured JSON       1.7 - 2.8s
        2300 characters of code     3.39s, not truncated
        three concurrent sessions   all fine, 4.77s wall clock
    REST text models, same prompt, same afternoon:
        gemini-2.5-flash-lite       0.76s   ...then 429, quota exhausted
        gemini-2.5-flash            0.80s   ...then 429
        gemini-flash-lite-latest    2.58s
        gemini-flash-latest         504, every time
    So REST is two to four times quicker while it lasts, and the whole point of
    the ladder is that it does not last. Pinned REST names sit behind Live;
    rolling `-latest` aliases sit behind those, because they were the ones
    having a bad day.

    Where the answer depends only on stable input, cache it and neither pool is
    touched twice: `plugins/_whatsapp_core.py` is the worked example — one
    request per WhatsApp language for the lifetime of the install.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import threading
from pathlib import Path

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent

_KEY_FILE = _BASE / "config" / "api_keys.json"

# Ladders, tried left to right. Change a model HERE and the whole app follows.
FAST = "fast"      # short classification, extraction, one-line decisions
SMART = "smart"    # reasoning, generation, long documents, images
SEARCH = "search"  # grounded search — REST only, see below

# A rung that means "ask the Live model instead", through a short throwaway
# session rather than the REST text API.
#
# WHY IT LEADS
#     This is a voice assistant: the Live API is the dependency it already has,
#     and it draws on a DIFFERENT quota pool from the text models. On the free
#     tier the text pool is the one that runs out — an afternoon of ordinary use
#     exhausts it, and when it does, every one of these side calls fails and the
#     feature behind it dies. The Live pool is untouched by that.
#
# WHAT IT COSTS, MEASURED
#     connect                      0.24s
#     short structured JSON        1.7 - 2.8s   (REST: 0.76s)
#     2300 characters of code      3.39s, not truncated
#     three concurrent sessions    all fine, 4.77s wall clock
#     So it is two to four times slower than REST when REST is available, and
#     infinitely faster than REST when REST is out of quota.
#
# THE THING WORTH KNOWING
#     These models only speak — response_modalities=["TEXT"] is refused with a
#     1007. The reply comes back through output_transcription, which sounds like
#     it would mangle anything structured. It does not: it is the model's own
#     text of what it said, and it survived "Mum ❤ click here for contact info",
#     indented Python inside markdown fences, and src/utils/helpers_v2.py
#     character for character. That is what makes this usable at all.
#
#     What it cannot carry is grounding metadata, so grounded web search stays
#     on REST — see SEARCH.
LIVE = "live"

# Every model this key can reach, in the order to try them. A rung that runs
# out of quota is skipped for a while and the next one answers — which is the
# whole point: one model a day means one daily limit, a ladder means the sum of
# them. Names verified against the live model list rather than guessed.
# Order is measured, not assumed. Timed against this key, same prompt:
#     gemini-3.5-flash-lite      0.56s      gemini-2.5-flash        0.67s
#     gemini-3.1-flash-lite      0.60s      gemini-2.5-flash-lite   0.74s
#     gemini-flash-lite-latest   0.60s      gemini-3.5-flash        1.13s
#     gemini-3-flash-preview     504, after 14.7s of waiting
#     gemini-3.6-flash           504, after 12.0s
#     gemini-flash-latest        503 UNAVAILABLE
# The three that fail sit at the BOTTOM rather than being deleted: they are
# real quota when they are healthy, and a rung that is down is set aside by the
# cooldown after one attempt instead of being paid for on every call.
_LADDERS = {
    FAST: (LIVE,
           "gemini-2.5-flash-lite", "gemini-3.5-flash-lite",
           "gemini-3.1-flash-lite", "gemini-flash-lite-latest",
           "gemini-2.5-flash", "gemini-3.5-flash",
           "gemini-3.6-flash", "gemini-3-flash-preview"),
    SMART: (LIVE,
            "gemini-2.5-flash", "gemini-3.5-flash",
            "gemini-2.5-flash-lite", "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.6-flash", "gemini-3-flash-preview", "gemini-flash-latest"),
    # Grounded search needs response.candidates[...].grounding_metadata, which a
    # Live turn does not produce. REST only, and it says so rather than silently
    # returning an answer with no sources behind it.
    SEARCH: ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-2.5-flash-lite",
             "gemini-flash-latest"),
}

# The conversation's own model, and ONE careful fallback behind it.
#
# Deliberately not a long ladder. The Live quota is not the one that runs out —
# the text models are — and Live models are not interchangeable the way text
# models are: they differ in which config fields they accept and in what they
# can do, so falling through a list of them risks connecting to something that
# behaves like a different assistant. The key also offers transcribe-live,
# live-translate and a robotics streaming model, none of which are assistants
# at all.
#
# So there are two: the current one, and the native-audio model this assistant
# used before it, which is known to work here. A mismatch in config is already
# survivable — the connect loop drops the tuning and proactive-audio fields and
# reconnects when the server rejects them.
LIVE_MODELS = (
    "models/gemini-3.1-flash-live-preview",
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
)

# The Live model to use for one-shot calls. main.py owns the real one; this is
# only the fallback for when this module is imported without it (tests).
_LIVE_FALLBACK = "models/gemini-3.1-flash-live-preview"

# ── Provider rungs ───────────────────────────────────────────────────────────
# An external, OpenAI-compatible model joined to the same ladder as the Gemini
# names, so a strong coder model can lead the SMART tier while every Gemini
# name stays behind it as a fallback. A rung is the string "provider:<pid>";
# the part after the prefix is the id that providers/registry.build and
# config_manager.get_provider_config both key on. Configuration lives in
# config/api_keys.json under "providers" — see memory/config_manager.py.
#
# Seconds a provider rung may take before the ladder moves on. A voice
# assistant cannot sit through a 45-second stall waiting for a model that
# is still loading: after this the next rung — usually a Gemini name —
# answers instead, and the slow rung is benched for a few minutes so the
# wait is paid once, not on every call. One number, one meaning; a
# per-caller timeout would let the longest caller decide how long the user
# waits, which is exactly what this exists to prevent.
#
# That is the FLOOR, not the whole wall. Above it stack the caller's own
# patience (code_helper files 60s for a code file) and, above that, the
# provider's own `wall_s` from config — because an aggregator on `model:
# auto` that is re-routing between a dozen upstream providers is not slow,
# it is WORKING, and cutting it off mid-route is how every heavy task ends
# up on another model's shoulders. A provider that is down says so in under
# a second, so patience costs nothing in the case it exists for. The hard
# ceiling below keeps any configuration from parking the ladder forever.
PROVIDER_RUNG_TIMEOUT_S = 20.0
# Hard ceiling for the wall clock below — no caller can park the ladder
# longer, however patient it claims to be.
PROVIDER_MAX_WALL_S = 300.0
PROVIDER_PREFIX = "provider:"

# ── Which brain answered ────────────────────────────────────────────────────
# A provider rung is invisible by nature: a heavy task is sent, some model
# answers, and nothing on screen distinguishes a provider turn from a Gemini
# turn. The natural follow-up — "is my external model actually being used?" —
# has no answer without this. Every successful turn therefore announces
# itself: the console always gets the line, and the app mirrors it into the
# HUD log panel by wiring set_brain_log_hook(ui.write_log). Failures already
# speak up in call()'s except chain; this is the other half of the picture.
_brain_hook = None

# ── who answered last, and who failed ───────────────────────────────────────
# The HUD badge and the "model apa yang dipakai" answer both read THIS, not
# the connect-time guess: a turn answered by the custom provider and a turn
# answered by Gemini are different models, and the interface says which one
# actually did the work.
_LAST_BRAIN: dict = {"kind": "", "model": "", "pid": "", "ts": 0.0}
_provider_failure_hook = None


def set_brain_log_hook(fn) -> None:
    """fn(line) receives one line per answered turn. None detaches."""
    global _brain_hook
    _brain_hook = fn


def set_provider_failure_hook(fn) -> None:
    """fn(pid, error) fires when a CUSTOM provider rung fails. The caller
    decides what to say — main.py speaks a short notice and moves the badge
    to Gemini, which is the model actually answering from then on."""
    global _provider_failure_hook
    _provider_failure_hook = fn


def last_brain() -> dict:
    """{kind: 'provider'|'gemini'|'', model, pid, ts} — the last answered turn."""
    return dict(_LAST_BRAIN)


def _note_brain(kind: str, model: str, pid: str = "") -> None:
    global _LAST_BRAIN
    _LAST_BRAIN = {"kind": kind, "model": model, "pid": pid,
                   "ts": time.time()}


def _announce_brain(label: str) -> None:
    line = f"[Brain] {label}"
    try:
        print(line)
    except Exception:
        pass
    fn = _brain_hook
    if fn is not None:
        try:
            fn(line)
        except Exception:
            pass


def _provider_pid(rung: str) -> str:
    return rung[len(PROVIDER_PREFIX):] if rung.startswith(PROVIDER_PREFIX) else rung


def _provider_rungs(tier: str) -> list[str]:
    """Provider rungs serving this tier, from config. Never raises — a
    config problem must not take the whole ladder down with it."""
    try:
        from memory.config_manager import load_api_keys
        providers = load_api_keys().get("providers")
        if not isinstance(providers, dict):
            return []
        out = []
        for pid, cfg in providers.items():
            if not isinstance(cfg, dict) or not cfg.get("model"):
                continue          # an entry without a model is not a rung
            tiers = cfg.get("tiers")
            if isinstance(tiers, str):
                tiers = [tiers]
            if isinstance(tiers, (list, tuple)):
                wanted = {str(t).strip().lower() for t in tiers}
                if tier not in wanted:
                    continue
            out.append(PROVIDER_PREFIX + str(pid))
        # The provider the user picked in the interface leads; the rest stay
        # on the ladder behind it as fallbacks. Manual switching therefore
        # works without removing anything — the pick is just the order.
        active = str(load_api_keys().get("active_provider") or "").strip()
        if active:
            pref = PROVIDER_PREFIX + active
            if pref in out:
                out.remove(pref)
                out.insert(0, pref)
        return out
    except Exception:
        return []


def _provider_first() -> bool:
    """Whether a configured provider leads the ladder (True) or only backs
    the Gemini names up. Default: lead."""
    try:
        from memory.config_manager import load_api_keys
        return bool(load_api_keys().get("provider_first", True))
    except Exception:
        return False


def _provider_vision(pid: str) -> bool:
    """Can this provider's model look at images? Conservative default: no.
    An image sent to a text-only model is a wasted round trip at best and
    a hallucination at worst, so the flag must be set explicitly."""
    try:
        from memory.config_manager import get_provider_config
        return bool(get_provider_config(pid).get("vision"))
    except Exception:
        return False


def _contents_have_image(contents) -> bool:
    items = contents if isinstance(contents, (list, tuple)) else [contents]
    for item in items:
        if isinstance(item, dict):
            if "inline_data" in item:
                return True
            continue
        if getattr(item, "inline_data", None) is not None:
            return True
    return False


def _provider_can_handle(contents) -> bool:
    """False when the request carries a shape no chat endpoint can accept
    — a Gemini file reference, an unknown part. Images are fine; whether
    the model can READ them is the vision flag's business."""
    items = contents if isinstance(contents, (list, tuple)) else [contents]
    for item in items:
        if isinstance(item, str):
            continue
        if isinstance(item, dict):
            if "text" in item or "inline_data" in item:
                continue
            return False
        if getattr(item, "text", None) is not None:
            continue
        if getattr(item, "inline_data", None) is not None:
            continue
        return False
    return True


def _system_of(config) -> str:
    """The system instruction off a config, dict or SDK object — the same
    two shapes _live_call already accepts."""
    if config is None:
        return ""
    system = getattr(config, "system_instruction", None)
    if system is None and isinstance(config, dict):
        system = config.get("system_instruction")
    return str(system or "")


def _to_openai_messages(contents, system: str) -> list:
    """REST `contents` -> OpenAI chat messages. Text parts become text;
    inline images become base64 data URLs. Mirrors _to_live_parts, for
    the endpoint on the other side."""
    import base64

    items = contents if isinstance(contents, (list, tuple)) else [contents]
    parts: list[dict] = []
    for item in items:
        if isinstance(item, str):
            parts.append({"type": "text", "text": item})
            continue
        blob = None
        if isinstance(item, dict):
            if "text" in item:
                parts.append({"type": "text", "text": str(item["text"])})
                continue
            inline = item.get("inline_data")
            blob = inline if isinstance(inline, dict) else None
        else:
            txt = getattr(item, "text", None)
            if txt and getattr(item, "inline_data", None) is None:
                parts.append({"type": "text", "text": str(txt)})
                continue
            blob = getattr(item, "inline_data", None)
        if blob is not None:
            mime = (blob.get("mime_type") if isinstance(blob, dict)
                    else getattr(blob, "mime_type", None)) or "application/octet-stream"
            data = (blob.get("data") if isinstance(blob, dict)
                    else getattr(blob, "data", None))
            if isinstance(data, (bytes, bytearray)):
                data = base64.b64encode(bytes(data)).decode("ascii")
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{mime};base64,{data}"}})

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    if len(parts) == 1 and parts[0]["type"] == "text":
        messages.append({"role": "user", "content": parts[0]["text"]})
    elif parts:
        messages.append({"role": "user", "content": parts})
    return messages


def _provider_call(rung: str, contents, config, timeout_ms: int):
    """One generation through a configured provider, shaped like the REST
    reply so every existing call site keeps working unchanged.

    The wall clock is enforced HERE, not in the request: a read timeout
    only fires between bytes, so a response that trickles — or a model
    that is still loading — never trips one. The call runs on its own
    thread and is joined after the wall clock: PROVIDER_RUNG_TIMEOUT_S,
    then the caller's own timeout when it is longer (a caller that filed a
    60s deadline for a code file means it), then the provider's configured
    `wall_s` when it is longer still — capped at PROVIDER_MAX_WALL_S. A
    rung still running at that point has lost its turn and the ladder
    moves on. The orphaned thread keeps the caller's own read timeout and
    dies with the process, exactly as the Live one-shot does."""
    from memory.config_manager import get_provider_config
    from providers import registry

    pid = _provider_pid(rung)
    cfg = get_provider_config(pid) or {}
    llm = registry.build(pid, cfg)
    messages = _to_openai_messages(contents, _system_of(config))
    if not messages:
        return None
    read_timeout = max(10.0, timeout_ms / 1000.0)
    # The wall clock, in layers: the 20s floor, the caller's own patience
    # above it, and the provider's configured `wall_s` above that. Each
    # layer only ever RAISES the wait — a slow-but-alive model finishes its
    # work instead of being benched for being as slow as its caller said
    # it could be. A dead provider fails in milliseconds, so the cost of
    # patience is paid only when something is actually being built.
    patience = float(cfg.get("wall_s") or 0.0)
    wall_s = min(max(PROVIDER_RUNG_TIMEOUT_S, read_timeout, patience),
                 PROVIDER_MAX_WALL_S)
    read_timeout = max(read_timeout, wall_s)   # the orphan must outlive the wall
    box: dict = {}

    def runner():
        try:
            text, _calls = llm.complete(messages, tools=None, timeout=read_timeout)
            box["text"] = text
        except BaseException as e:                     # noqa: BLE001
            box["error"] = e

    t0 = time.monotonic()
    th = threading.Thread(target=runner, daemon=True, name=f"provider-{pid}")
    th.start()
    th.join(wall_s)
    if th.is_alive():
        raise RuntimeError(
            f"provider '{pid}' did not answer within "
            f"{wall_s:.0f}s")
    if "error" in box:
        raise box["error"]
    text = box.get("text")
    if not text:
        return None
    # The duration is part of the proof: a patient provider answering in
    # 45s is the feature working, and the user deserves to see that it was
    # worth the wait rather than guessing.
    _note_brain("provider", str(cfg.get("model", "?")), pid)
    _announce_brain(f"provider '{pid}' answered this turn "
                    f"(model {cfg.get('model', '?')}, {time.monotonic() - t0:.1f}s)")
    return _Reply(text)

# How many one-shot Live sessions may exist at once.
#
# THE USER'S CONVERSATION OUTRANKS EVERY SIDE CALL.
# Nothing here can reach the microphone or the speaker — main.py has exactly one
# `.receive()` and it is bound to its own session, and audio only reaches the
# speaker through that one loop — so a side call cannot answer the user or talk
# over the reply. Verified alongside a live main session: four side calls fired
# while it was connected, each got its own answer, and the main session replied
# correctly both before and after with no errors.
#
# What a side call CAN do is take up a concurrent-session slot. That is the one
# way it could hurt the conversation, so it is capped, and a call that cannot
# get a slot quickly does not queue behind the others — it falls to the REST
# rung, which is what the ladder is for.
# Three, from the measurement: four side calls plus the conversation ran
# together without complaint, so three leaves the conversation a slot in hand
# while still covering any burst this app actually produces — tool calls run one
# after another, and the screen agent's loop is sequential.
_LIVE_SLOTS = threading.BoundedSemaphore(3)
_LIVE_SLOT_WAIT = 3.0

_ONE_SHOT_SYSTEM = (
    "You are a data-processing function, not an assistant and not in a "
    "conversation. There is no person listening to you. Produce exactly the "
    "output the request asks for and nothing else: no greeting, no "
    "acknowledgement, no 'Understood', no explanation, no closing remark, no "
    "restating of the question. If the request asks for JSON, emit only the "
    "JSON. If it asks for code, emit only the code. If it asks for one word, "
    "emit that one word. Preserve the exact spelling, punctuation, capitals "
    "and whitespace of anything you are asked to copy or return."
)

# Milliseconds. Not a preference: the API rejects anything under ten seconds
# with "Minimum allowed deadline is 10s", so this is the tightest bound it will
# accept. Callers with a long job (a whole document, a big image) pass more.
DEFAULT_TIMEOUT_MS = 10_000
MIN_TIMEOUT_MS = 10_000

_key_lock = threading.Lock()
_cached_key: str | None = None

# A rung that answered 429 is out of quota, and on the free tier it will stay
# that way for a while. Retrying it on every single call is a wasted round trip
# in front of every request the assistant makes — measured on this key, the
# lite rung was 429ing continuously, so every call was paying for it before
# reaching the model that could actually answer. Remembering that for a few
# minutes turns the ladder from a cost into a saving.
_COOLDOWN_SECONDS = 300
# A model that does not exist, or that this key may not use, is not coming back
# in five minutes. Retrying it on every call is a wasted round trip in front of
# everything the assistant does, so it is set aside for the session instead.
_GONE_SECONDS = 6 * 60 * 60
# Overloaded or timing out. Usually passes, so a shorter rest than "gone" —
# but long enough that the fourteen-second wait is paid once, not repeatedly.
_UNAVAILABLE_SECONDS = 30 * 60
# A rung that accepted the request but did not answer in time — a hung
# connection, a model still loading, a trickle that never finishes. Rested
# like an overloaded one but for less long: an overloaded model is expected
# back within the half hour, while a slow one is usually having a bad
# stretch — five minutes is long enough that the wait is paid once and not
# on every call, short enough that a provider which recovers quickly is not
# benched for half an hour.
_SLOW_SECONDS = 5 * 60
_cooldown: dict[str, float] = {}
_cool_lock = threading.Lock()


def _cool(model: str, seconds: float = _COOLDOWN_SECONDS) -> None:
    with _cool_lock:
        _cooldown[model] = time.monotonic() + seconds


def is_quota_error(err: str) -> bool:
    return "429" in err or "RESOURCE_EXHAUSTED" in err


def is_unavailable_error(err: str) -> bool:
    """The model is up but not answering — overloaded, or a deadline expired.

    Worth its own case because of what it costs: a 504 from one of these took
    fourteen seconds to arrive. Retrying that on every call puts the wait in
    front of everything the assistant does, so a rung that times out is rested
    like an exhausted one — for less long, since it is usually passing.
    """
    low = err.lower()
    return ("503" in err or "504" in err
            or "unavailable" in low or "deadline_exceeded" in low)


def is_slow_error(err: str) -> bool:
    """The rung took the request and never came back in time.

    Distinct from 'unavailable' by what rests it: an overloaded model is
    expected back within the half hour, while a timed-out one is having a
    bad stretch — five minutes, so the wait is paid once instead of on
    every call. Covers the two shapes a slow rung actually produces: a
    read timeout ("Read timed out") and the wall-clock cut in
    _provider_call and _live_call ("did not answer within Ns").
    """
    low = err.lower()
    return ("timed out" in low or "timeout" in low
            or "did not answer" in low)


def is_gone_error(err: str) -> bool:
    """The model is not there, or not ours to use — a different thing from busy.

    401 joins 403/404: a rejected key will not be accepted a minute from
    now either, so resting the rung beats paying for it on every call."""
    low = err.lower()
    return ("404" in err or "not found" in low or "is not supported" in low
            or "permission" in low or "403" in err or "401" in err)


def live_model() -> str:
    """The Live model to open the conversation with: the first rung that is not
    resting. If every one is resting the ladder is used from the top anyway —
    refusing to connect at all is never the better answer."""
    for m in LIVE_MODELS:
        if not _cooling(m):
            return m
    return LIVE_MODELS[0]


def note_live_failure(model: str, err: str) -> bool:
    """Record why a Live model failed. True when it is worth trying the next.

    Only quota and availability move the ladder along. A bad API key or a
    dropped network is not the model's fault, and stepping down the ladder for
    those would work through every model and reach the same wall four times.
    """
    if is_quota_error(err):
        _cool(model, _COOLDOWN_SECONDS)
        print(f"[Gemini] Live model {model} is out of quota — "
              f"switching for {_COOLDOWN_SECONDS // 60} minutes.")
        return True
    if is_gone_error(err):
        _cool(model, _GONE_SECONDS)
        print(f"[Gemini] Live model {model} is unavailable to this key — "
              f"setting it aside.")
        return True
    return False


def _cooling(model: str) -> bool:
    with _cool_lock:
        until = _cooldown.get(model, 0.0)
        if until and time.monotonic() < until:
            return True
        _cooldown.pop(model, None)
        return False


def api_key(refresh: bool = False) -> str:
    """The Gemini key from config/api_keys.json. Cached; never raises."""
    global _cached_key
    with _key_lock:
        if _cached_key is not None and not refresh:
            return _cached_key
        try:
            data = json.loads(_KEY_FILE.read_text(encoding="utf-8"))
            _cached_key = str(data.get("gemini_api_key") or "")
        except Exception:
            _cached_key = ""
        return _cached_key


def client(timeout_ms: int = DEFAULT_TIMEOUT_MS, key: str = ""):
    """A configured genai.Client with a deadline on it. Raises if there is no
    key, because a caller that cannot work without one should say so."""
    from google import genai
    from google.genai import types as gtypes

    key = key or api_key()
    if not key:
        raise RuntimeError("no Gemini API key is configured")
    return genai.Client(
        api_key=key,
        http_options=gtypes.HttpOptions(timeout=max(MIN_TIMEOUT_MS, int(timeout_ms))),
    )


class _Reply:
    """What a Live turn hands back, shaped like the REST response's `.text` so
    every existing call site keeps working unchanged."""

    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


def _live_model() -> str:
    """Whatever main.py is running, so upgrading the assistant upgrades this."""
    return getattr(sys.modules.get("main"), "LIVE_MODEL", None) or _LIVE_FALLBACK


def _to_live_parts(contents) -> list:
    """REST `contents` -> Live `parts`. Accepts a bare string, a list of
    strings, and the SDK's Part objects (which is how every image is passed
    here), because those are the three shapes the call sites actually use."""
    import base64

    items = contents if isinstance(contents, (list, tuple)) else [contents]
    parts = []
    for item in items:
        if isinstance(item, str):
            parts.append({"text": item})
            continue
        blob = getattr(item, "inline_data", None)
        if blob is not None:
            data = getattr(blob, "data", None)
            mime = getattr(blob, "mime_type", None) or "application/octet-stream"
            if isinstance(data, bytes):
                data = base64.b64encode(data).decode("ascii")
            parts.append({"inline_data": {"mime_type": mime, "data": data}})
            continue
        txt = getattr(item, "text", None)
        if txt:
            parts.append({"text": txt})
            continue
        if isinstance(item, dict):
            parts.append(item)
    return parts


async def _live_turn(parts: list, system: str, key: str, timeout_s: float) -> str:
    from google import genai
    from google.genai import types as gtypes

    cl = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
    # Silence the persona, or it answers instead of complying.
    #
    # These are conversational models and they behave like it: asked "Reply with
    # one word: ok" a Live turn came back with "Understood." — it treated the
    # instruction as something to acknowledge rather than something to do. The
    # REST models do not, because nobody ever taught them to be in a
    # conversation. Every call through this module wants a value, not a reply,
    # so the session is told what it is before it is told what to do.
    kwargs = {
        "response_modalities": ["AUDIO"],
        "output_audio_transcription": {},
        "system_instruction": _ONE_SHOT_SYSTEM + (f"\n\n{system}" if system else ""),
    }

    cm = cl.aio.live.connect(model=_live_model(),
                             config=gtypes.LiveConnectConfig(**kwargs))
    session = await asyncio.wait_for(cm.__aenter__(), 30)
    try:
        await session.send_client_content(
            turns={"role": "user", "parts": parts}, turn_complete=True)
        chunks: list[str] = []

        async def drain():
            async for resp in session.receive():
                sc = getattr(resp, "server_content", None)
                if sc and sc.output_transcription and sc.output_transcription.text:
                    chunks.append(sc.output_transcription.text)

        await asyncio.wait_for(drain(), timeout=timeout_s)
        # The transcription can trail the audio turn by a beat; a short second
        # drain stops a reply being cut mid-token. chat_takeover learned this
        # the same way and for the same reason.
        try:
            await asyncio.wait_for(drain(), timeout=1.5)
        except asyncio.TimeoutError:
            pass
        return "".join(chunks).strip()
    finally:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            pass


def _live_call(contents, config, timeout_ms: int, key: str):
    """One throwaway Live session, run on its own loop in its own thread.

    A dedicated thread rather than asyncio.run() on the caller's: these are
    invoked from plugin executor threads, from UI worker threads and from
    main.py's own event loop, and asyncio.run() inside a thread that already has
    a running loop raises. Its own thread has no loop to collide with, wherever
    it was called from.

    Reconnecting costs 0.24s, so nothing is kept alive between calls — no
    session lifetime cap to manage, no GoAway to handle, no shared state.
    """
    system = ""
    if config is not None:
        system = getattr(config, "system_instruction", None) or \
            (config.get("system_instruction") if isinstance(config, dict) else "") or ""

    parts = _to_live_parts(contents)
    if not parts:
        return None

    if not _LIVE_SLOTS.acquire(timeout=_LIVE_SLOT_WAIT):
        # Every slot is busy. Do not wait it out: falling to REST costs less
        # than holding a session the user's conversation might want.
        raise RuntimeError("no free Live slot — leaving them for the conversation")

    box: dict = {}

    def runner():
        try:
            box["text"] = asyncio.run(
                _live_turn(parts, str(system), key, max(10.0, timeout_ms / 1000.0)))
        except BaseException as e:                     # noqa: BLE001
            box["error"] = e

    try:
        th = threading.Thread(target=runner, daemon=True, name="gemini-live-oneshot")
        th.start()
        th.join(timeout=max(15.0, timeout_ms / 1000.0 + 20.0))
    finally:
        _LIVE_SLOTS.release()
    if th.is_alive():
        # The join gave up but the turn is still running. Benched, not
        # retried on the next call — the wait is paid once.
        raise RuntimeError(
            "the Live one-shot did not answer within "
            f"{max(15.0, timeout_ms / 1000.0 + 20.0):.0f}s")
    if "error" in box:
        raise box["error"]
    text = box.get("text")
    return _Reply(text) if text else None


def call(contents, tier: str = FAST, config=None,
         timeout_ms: int = DEFAULT_TIMEOUT_MS, key: str = ""):
    """Run one generation, walking the ladder until one answers.

    Returns the SDK's own response object, so callers that need more than the
    text — grounding metadata, candidates, usage — still get it. Returns None
    when every model on the ladder failed; the reason for each is printed, since
    a silent None during a session nobody can debug is how the original problem
    stayed hidden.
    """
    # `tier` is normally FAST or SMART. Anything else is taken to be an explicit
    # model name — screen_agent lets the user pick one in its settings — and it
    # is tried first, with the reasoning ladder behind it. So a user's choice is
    # honoured, and a user's choice that is having an outage still degrades to
    # something that answers instead of to nothing.
    ladder = _LADDERS.get(tier)
    if ladder is None:
        ladder = (tier,) + tuple(m for m in _LADDERS[SMART] if m != tier)

    resolved_key = key or api_key()

    # ── Provider rungs ───────────────────────────────────────────────────
    # An external, OpenAI-compatible model on the same ladder as the Gemini
    # names. The user's order decides whether it leads (heavy lifting done
    # elsewhere — a coder model on the SMART tier) or only backs the Gemini
    # names up. A rung the request cannot use is skipped, never attempted:
    # a text-only model is never sent an image, and a request carrying a
    # Gemini file reference has no chat-endpoint form at all. With no
    # provider configured, this block is a no-op and the ladder is the
    # same ladder it has always been.
    prov = _provider_rungs(tier)
    if prov:
        has_image = _contents_have_image(contents)
        usable = [r for r in prov
                  if _provider_can_handle(contents)
                  and (not has_image or _provider_vision(_provider_pid(r)))]
        # A benched provider is skipped, full stop — that is what benching
        # is FOR. Unlike the Gemini ladder below (where "try them all if
        # every one is resting" exists so the conversation still connects),
        # there is always something behind a provider rung to answer with,
        # so paying the wait again would be the waste this whole mechanism
        # removes. The one exception: with no Gemini key behind it at all,
        # a benched rung still beats no answer.
        prov = [r for r in usable if not _cooling(r)]
        if not prov and not resolved_key and usable:
            prov = usable
    gemini_rungs = [m for m in ladder if not _cooling(m)] or list(ladder)
    if not resolved_key:
        # Nothing to authenticate the Gemini rungs with — the provider
        # rungs carry their own key, so they still answer.
        gemini_rungs = []
    tried = ((prov + gemini_rungs) if (prov and _provider_first())
             else (gemini_rungs + prov))
    if not tried:
        print("[Gemini] no Gemini API key is configured and no provider is set up")
        return None

    cl = None
    for model in tried:
        try:
            if model.startswith(PROVIDER_PREFIX):
                reply = _provider_call(model, contents, config, timeout_ms)
                if reply is not None:
                    return reply
                raise RuntimeError("the provider turn came back empty")
            if model == LIVE:
                reply = _live_call(contents, config, timeout_ms, resolved_key)
                if reply is not None:
                    _note_brain("gemini", model)
                    _announce_brain(f"Gemini Live answered (model {model})")
                    return reply
                raise RuntimeError("the Live turn came back empty")
            if cl is None:
                cl = client(timeout_ms=timeout_ms, key=resolved_key)
            kwargs = {"model": model, "contents": contents}
            if config is not None:
                kwargs["config"] = config
            resp = cl.models.generate_content(**kwargs)
            _note_brain("gemini", model)
            _announce_brain(f"{model} answered")
            return resp
        except Exception as e:
            msg = str(e)
            if model.startswith(PROVIDER_PREFIX):
                # Tell the caller, which speaks a short notice: the user
                # asked for a custom model and is about to be served by
                # Gemini instead, and should hear that rather than wonder.
                fn = _provider_failure_hook
                if fn is not None:
                    try:
                        fn(_provider_pid(model), msg)
                    except Exception:
                        pass
            if is_quota_error(msg):
                _cool(model)
                print(f"[Gemini] {model}: out of quota — skipping it for "
                      f"{_COOLDOWN_SECONDS // 60} minutes")
            elif is_gone_error(msg):
                _cool(model, _GONE_SECONDS)
                print(f"[Gemini] {model}: unavailable to this key — set aside")
            elif is_unavailable_error(msg):
                _cool(model, _UNAVAILABLE_SECONDS)
                print(f"[Gemini] {model}: not answering — resting it for "
                      f"{_UNAVAILABLE_SECONDS // 60} minutes")
            elif is_slow_error(msg):
                _cool(model, _SLOW_SECONDS)
                print(f"[Gemini] {model}: too slow — resting it for "
                      f"{_SLOW_SECONDS // 60} minutes")
            else:
                print(f"[Gemini] {model}: {type(e).__name__}: {msg[:140]}")
    return None


def text(contents, tier: str = FAST, config=None,
         timeout_ms: int = DEFAULT_TIMEOUT_MS, key: str = "", default: str = "") -> str:
    """`call`, reduced to the reply text. `default` when nothing answered."""
    resp = call(contents, tier=tier, config=config,
                timeout_ms=timeout_ms, key=key)
    if resp is None:
        return default
    return (getattr(resp, "text", None) or "").strip() or default


def as_json(contents, tier: str = FAST, config=None,
            timeout_ms: int = DEFAULT_TIMEOUT_MS, key: str = "", default=None):
    """`text`, parsed as JSON, tolerating the fences and prose a model wraps it
    in. `default` when nothing answered or the answer would not parse."""
    raw = text(contents, tier=tier, config=config, timeout_ms=timeout_ms, key=key)
    if not raw:
        return default
    if "{" in raw and "}" in raw:
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
    elif "[" in raw and "]" in raw:
        raw = raw[raw.find("["): raw.rfind("]") + 1]
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"[Gemini] reply was not JSON: {e}")
        return default
