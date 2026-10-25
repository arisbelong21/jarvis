"""
meeting_notetaker.py — record the call, get the notes.

WHY THIS IS THE ONE THAT NEEDED A PROBE
    Everything else here is software; this needed the hardware to answer a
    question first. Capturing what the PC is PLAYING is not the same as
    recording a microphone, and on Windows the honest answer is "it depends":
    a Stereo Mix device only mirrors the sound card it belongs to, so a USB or
    Bluetooth headset routes audio straight past it. That was measured on this
    machine — a played tone reached the room microphones at full level and the
    Realtek Stereo Mix at 1/50th of it. The reliable route is WASAPI loopback
    on the ACTIVE output endpoint, which pyaudiowpatch exposes and which was
    verified here: the default endpoint was found and a played tone came back
    at 0.81 peak. So the cascade below tries, in order:

        1. pyaudiowpatch WASAPI loopback  (the verified, device-independent way)
        2. any "Stereo Mix" / "Loopback" input device via sounddevice
        3. the default microphone        (last resort, announced out loud)

WHAT IT SAVES AND WHAT IT THROWS AWAY
    Audio is written as 16 kHz mono — the shape speech transcription wants, a
    quarter of the bytes of the raw stream. On stop, the recording is chunked,
    transcribed, summarised, and the SUMMARY is what is kept: a markdown file
    in memory/meetings/ plus one rolling memory note. The audio file itself is
    deleted unless "keep the audio" is switched on, because a folder of
    meeting recordings is a liability nobody asked for.

WHERE IT CANNOT HELP
    Recording someone's call may require their consent where you live, and the
    plugin cannot know that. It records what the machine plays; whether you
    may is your call, and it says so once, in the settings label.
"""

import io
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from core import gemini as _gemini

_BASE = Path(__file__).resolve().parent.parent
_MEET_DIR = _BASE / "memory" / "meetings"
_NS = "meeting_notetaker"
_DST_RATE = 16000           # speech rate: what the transcriber wants
_READ_BLOCK = 1024

PLUGIN = {
    "name": "meeting_notetaker",
    "description": (
        "Records a meeting, call, class or video and turns it into written "
        "notes: transcript plus summary, key points, decisions and action "
        "items, saved as a file. It records what the COMPUTER is playing "
        "(WASAPI loopback), so a Zoom/Meet/Discord call is captured without a "
        "cable. Use for: 'start taking notes', 'record this meeting', 'note "
        "this call', 'stop recording', 'summarise the meeting', 'what did we "
        "decide'. Do NOT use for a single spoken thought — that is the journal."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'start' begin recording, 'stop' finish and produce the "
                    "notes, 'status' say whether it is recording and for how "
                    "long, 'list' show past meetings, 'summarise' re-summarise "
                    "the most recent recording."
                ),
            },
            "title": {
                "type": "STRING",
                "description": "For 'start': a short name for this meeting (optional).",
            },
        },
        "required": [],
    },
}

PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "🎙  MEETING NOTETAKER",
    "fields": [
        {"key": "source", "label": "What to record", "type": "choice",
         "options": ["System audio (loopback)", "Microphone", "Automatic"],
         "default": "Automatic"},
        {"key": "chunk_seconds", "label": "Transcribe in blocks of (seconds)",
         "type": "text", "default": "120", "placeholder": "120"},
        {"key": "max_minutes", "label": "Stop automatically after (minutes)",
         "type": "text", "default": "60", "placeholder": "60"},
        {"key": "keep_audio", "label": "Keep the audio file after the notes are made",
         "type": "toggle", "default": False},
        {"key": "privacy_note", "label": "Recording a call may need the other "
         "people's consent where you live — this plugin cannot know that.",
         "type": "label", "default": ""},
    ],
}


# ── settings ─────────────────────────────────────────────────────────────────

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


# ── streaming resampler (device rate → 16 kHz, mono) ─────────────────────────

class _Downsampler:
    """Block-wise linear resampler whose phase survives across blocks, so a
    long recording does not click at every 1024-sample boundary."""

    def __init__(self, src_rate: int, dst_rate: int = _DST_RATE):
        self.ratio = float(src_rate) / float(dst_rate)
        self.pos = 0.0
        self._tail = np.zeros(0, dtype=np.float32)

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        x = np.concatenate([self._tail, block]) if self._tail.size else block
        if x.size < 2:
            self._tail = x
            return np.zeros(0, dtype=np.float32)
        n_out = int(np.floor((x.size - 1 - self.pos) / self.ratio)) + 1
        if n_out <= 0:
            self._tail = x
            return np.zeros(0, dtype=np.float32)
        idx = self.pos + np.arange(n_out, dtype=np.float64) * self.ratio
        out = np.interp(idx, np.arange(x.size, dtype=np.float64), x).astype(np.float32)
        last = float(idx[-1])
        consumed = int(np.floor(last))
        self._tail = x[consumed:]
        self.pos = last - consumed
        return out


def _to_int16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


# ── capture backends ─────────────────────────────────────────────────────────

class _LoopbackSource:
    """pyaudiowpatch: WASAPI loopback on the active output endpoint."""

    def __init__(self):
        self._pa = None
        self._stream = None
        self.rate = 0
        self.channels = 0
        self.name = ""

    def open(self) -> bool:
        try:
            import pyaudiowpatch as pa
        except Exception:
            return False
        try:
            self._pa = pa.PyAudio()
            dev = self._pa.get_default_wasapi_loopback()
            self.rate = int(dev["defaultSampleRate"])
            self.channels = int(dev["maxInputChannels"])
            self.name = str(dev["name"])
            self._stream = self._pa.open(
                format=pa.paFloat32, channels=self.channels, rate=self.rate,
                input=True, input_device_index=dev["index"],
                frames_per_buffer=_READ_BLOCK)
            return True
        except Exception:
            self.close()
            return False

    def read(self) -> np.ndarray | None:
        try:
            raw = self._stream.read(_READ_BLOCK, exception_on_overflow=False)
            data = np.frombuffer(raw, dtype=np.float32)
            return data.reshape(-1, self.channels)
        except Exception:
            return None

    def close(self) -> None:
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            pass
        try:
            if self._pa:
                self._pa.terminate()
        except Exception:
            pass
        self._stream = self._pa = None


class _SoundDeviceSource:
    """A named input device (Stereo Mix / Loopback) or the default microphone."""

    def __init__(self, want_name: str = ""):
        self._want = want_name.lower()
        self._stream = None
        self.rate = 0
        self.channels = 0
        self.name = ""

    def open(self) -> bool:
        try:
            import sounddevice as sd
        except Exception:
            return False
        try:
            dev = None
            if self._want:
                for i, d in enumerate(sd.query_devices()):
                    if d["max_input_channels"] > 0 and self._want in d["name"].lower():
                        dev = i
                        break
            if dev is None:
                dev = sd.default.device[0]
            info = sd.query_devices(dev)
            self.rate = int(info["default_samplerate"]) or 48000
            self.channels = max(1, min(2, int(info["max_input_channels"])))
            self.name = str(info["name"])
            self._stream = sd.InputStream(device=dev, channels=self.channels,
                                          samplerate=self.rate, dtype="float32",
                                          blocksize=_READ_BLOCK)
            self._stream.start()
            return True
        except Exception:
            self.close()
            return False

    def read(self) -> np.ndarray | None:
        try:
            data, _ = self._stream.read(_READ_BLOCK)
            return np.asarray(data, dtype=np.float32)
        except Exception:
            return None

    def close(self) -> None:
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        self._stream = None


def _open_source(preference: str):
    """(source, human_label). Preference: loopback / microphone / automatic."""
    want_mic = preference.lower().startswith("micro")
    want_lb = preference.lower().startswith("system") or preference.lower().startswith("loop")
    if not want_mic:
        src = _LoopbackSource()
        if src.open():
            return src, f"system audio ({src.name})"
        for name in ("stereo mix", "loopback"):
            src = _SoundDeviceSource(name)
            if src.open():
                return src, f"system audio via {src.name}"
            src.close()
    if not want_lb or want_mic:
        src = _SoundDeviceSource()
        if src.open():
            return src, f"microphone ({src.name})"
        src.close()
    return None, "no audio input"


# ── recorder ─────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_rec: dict = {"active": False, "stop": None, "thread": None, "path": None,
              "started": 0.0, "title": "", "source": "", "auto_stopped": False}


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


def _record_worker(player, source, wav_path: Path, stop: threading.Event,
                   max_seconds: int) -> None:
    down = _Downsampler(source.rate)
    try:
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_DST_RATE)
            t0 = time.time()
            while not stop.is_set():
                if max_seconds and (time.time() - t0) >= max_seconds:
                    with _lock:
                        _rec["auto_stopped"] = True
                    break
                block = source.read()
                if block is None:
                    break
                mono = block.mean(axis=1) if block.ndim > 1 else block
                wf.writeframes(_to_int16(down.process(mono)))
    except Exception as e:
        _log(player, f"SYS: Meeting recording stopped on an error: {e}")
    finally:
        try:
            source.close()
        except Exception:
            pass
        with _lock:
            auto = _rec["auto_stopped"]
        if auto:
            _log(player, "SYS: Meeting recording hit the time limit — writing notes.")
            _say(player, "Tell the user the meeting recording reached its time "
                         "limit and you are writing the notes now. One sentence.")
            threading.Thread(target=_process_last, args=(player,), daemon=True,
                             name="meeting-notes").start()


# ── transcription + summary ──────────────────────────────────────────────────

_TRANSCRIBE_MODELS = ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-flash-latest")


def _transcribe_chunk(wav_bytes: bytes) -> str:
    """One audio block → text. Gemini directly (the provider rungs on the
    ladder are chat endpoints; audio is not their shape)."""
    try:
        from google import genai
        from google.genai import types
        key = _gemini.api_key()
        if not key:
            return "[no Gemini API key configured — cannot transcribe]"
        client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
        part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")
        prompt = ("Transcribe this audio VERBATIM, in the language spoken. "
                  "No commentary, no speaker labels unless a speaker is named. "
                  "Write [unclear] for anything unintelligible. Output only the "
                  "transcript.")
        last_err = ""
        for model in _TRANSCRIBE_MODELS:
            try:
                resp = client.models.generate_content(model=model,
                                                      contents=[prompt, part])
                text = (getattr(resp, "text", "") or "").strip()
                if text:
                    return text
                last_err = "empty response"
            except Exception as e:
                last_err = str(e)
                continue
        return f"[transcription failed: {last_err}]"
    except Exception as e:
        return f"[transcription failed: {e}]"


def _iter_wav_chunks(path: Path, chunk_seconds: int):
    """Yield (index, wav_bytes) for successive slices of a mono 16-bit WAV.

    Slices are at least 10 seconds long whatever is asked: a 1-second chunk is
    one API call per second of meeting, which is a bill, not a transcription."""
    per = _DST_RATE * max(10, chunk_seconds)
    with wave.open(str(path), "rb") as wf:
        n_frames = wf.getnframes()
        idx = 0
        while True:
            frames = wf.readframes(per)
            if not frames:
                break
            idx += 1
            buf = io.BytesIO()
            with wave.open(buf, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(_DST_RATE)
                out.writeframes(frames)
            yield idx, buf.getvalue()
            if wf.tell() >= n_frames:
                break


def _summarise(transcript: str, title: str) -> str:
    prompt = (
        "You are writing the notes of a meeting from its transcript.\n"
        f"Meeting title: {title or 'untitled'}\n\n"
        "Write markdown with these sections, in the language of the transcript:\n"
        "## Summary — 3 to 5 sentences\n"
        "## Key points — bullets\n"
        "## Decisions — bullets (say 'none stated' if there were none)\n"
        "## Action items — bullets, with who owns each if the transcript says\n"
        "## Open questions — bullets\n\n"
        "Be faithful: never invent a name, number or decision that is not in the "
        "transcript.\n\nTRANSCRIPT:\n" + transcript[:120000]
    )
    try:
        resp = _gemini.call(prompt, tier=_gemini.SMART, timeout_ms=120000)
        if resp is None:
            return "## Summary\n(could not summarise — every model on the ladder failed)"
        return (getattr(resp, "text", "") or "").strip()
    except Exception as e:
        return f"## Summary\n(summarising failed: {e})"


def _process_last(player, path: Path | None = None, title: str = "") -> str:
    """Transcribe + summarise one recording, save the notes, then honour the
    keep_audio setting. Safe to call from a worker thread."""
    with _lock:
        wav_path = path or _rec.get("path")
        title = title or _rec.get("title") or ""
    if not wav_path:
        return "There is no recording to write up."
    wav_path = Path(wav_path)
    if not wav_path.exists():
        return f"I can't find the recording at {wav_path}."

    chunk_seconds = _as_int(_setting("chunk_seconds", 120), 120, 30, 600)
    _log(player, f"SYS: Writing up {wav_path.name} …")
    _say(player, "Tell the user in one short sentence that you are transcribing "
                 "the recording now and will have the notes in a moment.")

    parts = []
    try:
        for idx, blob in _iter_wav_chunks(wav_path, chunk_seconds):
            piece = _transcribe_chunk(blob)
            parts.append(f"[part {idx}]\n{piece}")
            _log(player, f"SYS: Meeting transcript — part {idx} done.")
    except Exception as e:
        parts.append(f"[chunking failed: {e}]")

    transcript = "\n\n".join(parts).strip() or "(no speech detected)"
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    summary = _summarise(transcript, title)

    try:
        _MEET_DIR.mkdir(parents=True, exist_ok=True)
        md = _MEET_DIR / f"{wav_path.stem}.md"
        md.write_text(
            f"# {title or 'Meeting'} — {datetime.now():%Y-%m-%d %H:%M}\n\n"
            f"{summary}\n\n---\n\n## Transcript\n\n{transcript}\n",
            encoding="utf-8")
    except Exception as e:
        return f"The notes could not be saved: {e}"

    try:
        from memory.memory_manager import remember
        first = summary.splitlines()
        head = next((l for l in first if l.strip() and not l.startswith("#")), "")[:200]
        remember("last_meeting",
                 f"Meeting recorded on {stamp}{' — ' + title if title else ''}. "
                 f"Notes at {md}. Summary starts: {head}", "notes")
    except Exception:
        pass

    try:
        if player:
            player.show_content("🎙 MEETING NOTES", summary[:4000])
    except Exception:
        pass

    if not _setting("keep_audio", False):
        try:
            wav_path.unlink()
        except Exception:
            pass

    with _lock:
        _rec["path"] = None

    _log(player, f"SYS: Meeting notes saved to {md.name}.")
    return (f"Notes are ready. Summary:\n\n{summary[:1500]}"
            + ("\n\n(Full notes and transcript saved in memory/meetings.)" ))


# ── entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "start").strip().lower()
    try:
        if action in ("status", "info"):
            with _lock:
                active, started, src, title = (_rec["active"], _rec["started"],
                                               _rec["source"], _rec["title"])
            if not active:
                return "Not recording anything right now."
            mins = int((time.time() - started) // 60)
            return (f"Recording{' — ' + title if title else ''} for {mins} minute(s), "
                    f"capturing {src}. Say stop when the meeting ends.")

        if action in ("list", "meetings"):
            try:
                files = sorted(_MEET_DIR.glob("*.md"), reverse=True)[:10]
            except Exception:
                files = []
            if not files:
                return "No meetings have been written up yet."
            return "Recent meetings:\n" + "\n".join(f.name for f in files)

        if action in ("summarise", "summarize"):
            try:
                files = sorted(_MEET_DIR.glob("*.md"), reverse=True)
            except Exception:
                files = []
            if not files:
                return "There are no past meetings to summarise."
            text = files[0].read_text(encoding="utf-8", errors="replace")
            return f"Most recent meeting ({files[0].name}):\n\n" + text[:2500]

        if action == "stop":
            with _lock:
                if not _rec["active"]:
                    return "Nothing is being recorded."
                stop_ev = _rec["stop"]
                path = _rec["path"]
                title = _rec["title"]
                _rec["active"] = False
            if stop_ev:
                stop_ev.set()
            # Give the writer a moment to flush the last block into the file.
            for _ in range(20):
                if _rec["thread"] and not _rec["thread"].is_alive():
                    break
                time.sleep(0.1)
            return _process_last(player, path, title)

        # -------- START --------
        with _lock:
            if _rec["active"]:
                return "I'm already recording. Say stop when the meeting ends."

        preference = str(_setting("source", "Automatic"))
        source, label = _open_source(preference)
        if source is None:
            return ("I couldn't open any audio input, sir — no loopback device "
                    "and no microphone came up. If you asked for system audio "
                    "only, try 'pip install pyaudiowpatch'.")

        title = (parameters.get("title") or "").strip()
        _MEET_DIR.mkdir(parents=True, exist_ok=True)
        wav_path = _MEET_DIR / f"rec_{datetime.now():%Y-%m-%d_%H%M%S}.wav"
        stop_ev = threading.Event()
        max_seconds = _as_int(_setting("max_minutes", 60), 60, 1, 600) * 60

        th = threading.Thread(target=_record_worker,
                              args=(player, source, wav_path, stop_ev, max_seconds),
                              daemon=True, name="meeting-recorder")
        with _lock:
            _rec.update({"active": True, "stop": stop_ev, "thread": th,
                         "path": wav_path, "started": time.time(),
                         "title": title, "source": label, "auto_stopped": False})
        th.start()

        warn = ""
        if label.startswith("microphone"):
            warn = (" Careful: I'm on the microphone, so I'll only catch the room — "
                    "if the meeting audio is in your headphones, install "
                    "pyaudiowpatch so I can capture the system audio instead.")
        return (f"Recording started{' — ' + title if title else ''}, capturing "
                f"{label}. Say stop when you're done and I'll write up the notes."
                + warn)
    except Exception as e:
        return f"Sir, the meeting notetaker failed: {e}"
