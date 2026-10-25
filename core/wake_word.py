"""
Local wake-word detection for JARVIS ("Hey Jarvis").

Design goals:
  • ZERO cost when the feature is off — openwakeword is imported ONLY inside
    start()/install helpers, never at module load. If the user never enables
    wake word, none of this touches the app.
  • ZERO latency on the audio path — the microphone callback only ever does a
    cheap, non-blocking queue push (feed()); the actual model inference runs in
    this module's own background thread, so the real-time audio thread and the
    Gemini stream are never slowed.
  • Fully local & offline — audio fed here never leaves the machine; there is no
    network call except the one-time model download the user triggers from the UI.

openwakeword ships small ONNX models (a few MB each) and runs comfortably on a
CPU. The pretrained wake phrase used here is "Hey Jarvis".
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

# Pretrained openwakeword model that listens for "Hey Jarvis".
WAKE_MODEL = "hey_jarvis"
# Score in [0,1]; above this counts as a detection. Tunable per environment —
# and from config/api_keys.json ("wake_threshold"), see memory.config_manager.
#
# 0.35, not the library's 0.5, because the two mistakes are NOT equal here.
# A missed wake is what the user feels: "I called and called and nothing
# happened". A false wake costs almost nothing — wake() changes no audio and
# speaks nothing, the assistant simply stops sleeping (and stops sleeping
# again after the silence window). A soft voice, a quiet mic or an accent the
# English-trained model scores lower should not have to fight a strict bar.
DEFAULT_THRESHOLD = 0.35
# Console line showing the best recent score, so a user who is still not
# being heard can tell "my voice scores 0.28, the bar is 0.35" apart from
# "the detector never runs at all" — without guessing.
PEAK_LOG_INTERVAL_S = 10.0
# Mic frames arrive at 16 kHz int16; this is just the detector's input rate.
SAMPLE_RATE = 16000


def is_installed() -> bool:
    """True if the openwakeword package is importable (no model check)."""
    try:
        import importlib.util
        return importlib.util.find_spec("openwakeword") is not None
    except Exception:
        return False


def is_ready() -> bool:
    """True if openwakeword is installed AND its model files are present on disk.

    This is a cheap, DETERMINISTIC file-existence check. It deliberately does NOT
    construct a Model to probe readiness — doing that is slow and, worse, can clash
    with the detector's own Model when it's already running, which intermittently
    returned False and made the UI flicker to 'not downloaded'. Never raises.
    """
    if not is_installed():
        return False
    try:
        import openwakeword
        models_dir = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
        if not models_dir.is_dir():
            return False
        has_wake = (any(models_dir.glob(f"{WAKE_MODEL}*.onnx"))
                    or any(models_dir.glob(f"{WAKE_MODEL}*.tflite")))
        has_mel = (any(models_dir.glob("melspectrogram*.onnx"))
                   or any(models_dir.glob("melspectrogram*.tflite")))
        has_emb = (any(models_dir.glob("embedding_model*.onnx"))
                   or any(models_dir.glob("embedding_model*.tflite")))
        return bool(has_wake and has_mel and has_emb)
    except Exception:
        return False


def install_and_download(logger: Callable[[str], None] = print,
                         notify: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """
    One-click setup for the UI button: pip-install openwakeword if missing, then
    download the wake model. Returns (ok, message). Never raises — every failure
    is reported through the returned message and the logger.
    """
    _tell = notify or (lambda _msg: None)
    try:
        if not is_installed():
            logger("Wake word: installing openwakeword (one-time)…")
            _tell("Wake word: installing openwakeword (one-time)…")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "openwakeword"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
                return False, f"pip install failed: {tail[0][:160]}"
        # Download the pretrained melspectrogram/embedding + wake models.
        logger("Wake word: downloading models…")
        _tell("Wake word: downloading models…")
        try:
            import openwakeword.utils as _u
            try:
                _u.download_models([WAKE_MODEL])
            except TypeError:
                _u.download_models()   # older signature downloads the default set
        except Exception as e:
            return False, f"model download failed: {e}"

        if not is_ready():
            return False, "installed, but the wake model could not be loaded."
        logger("Wake word: ready.")
        return True, "Wake word installed and ready."
    except Exception as e:
        return False, f"setup error: {e}"


class WakeWordDetector:
    """
    Runs the wake model in a dedicated thread. The mic thread calls feed() with
    raw int16 frames; detections invoke on_detect() (called from this thread —
    the callback must marshal to whatever loop/UI it needs).
    """

    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = DEFAULT_THRESHOLD,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None):
        self._on_detect = on_detect
        self._threshold = threshold
        self._logger    = logger
        # See PluginRegistry: `logger` is the console and gets everything,
        # `notify` is the activity log and gets only what the user must act on.
        self._notify    = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None
        self._running = False
        self._model = None
        self._ready = False
        self._peak = 0.0                 # best score since the last console line
        self._last_peak_log = time.monotonic()

    def start(self) -> bool:
        """Load the model and spawn the inference thread. Returns True on success.
        Safe to call again — a no-op if already running. Never raises."""
        if self._running:
            return True
        try:
            from openwakeword.model import Model
            self._model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
        except Exception as e:
            # A fresh virtualenv installs the PACKAGE but not its weights —
            # exactly what happened after the Python 3.13 migration: the code
            # was there, hey_jarvis_v0.1.onnx was not, and the wake word
            # silently never worked. Fetch the model once and retry.
            msg = str(e)
            if ("NO_SUCHFILE" in msg or "doesn't exist" in msg
                    or "no such file" in msg.lower()):
                self._logger("Wake word: model belum ada — mengunduh sekali…")
                try:
                    import openwakeword.utils as _u
                    try:
                        _u.download_models([WAKE_MODEL])
                    except TypeError:
                        _u.download_models()
                    from openwakeword.model import Model
                    self._model = Model(wakeword_models=[WAKE_MODEL],
                                        inference_framework="onnx")
                    self._logger("Wake word: model diunduh & siap.")
                except Exception as e2:              # noqa: BLE001
                    self._logger(f"Wake word: unduh model gagal — {e2}")
                    self._notify("Wake word: model belum ada — buka ⚙ → "
                                 "WAKE WORD lalu tekan unduh.")
                    self._model = None
                    return False
            else:
                self._logger(f"Wake word: could not load model — {e}")
                self._notify("Wake word unavailable — use the WAKE NOW button.")
                self._model = None
                return False
        self._running = True
        self._ready = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="WakeWordThread")
        self._thread.start()
        self._logger("Wake word: listening for 'Hey Jarvis'.")
        return True

    def stop(self) -> None:
        self._running = False
        # unblock the thread if it's waiting on the queue
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        self._model = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def feed(self, frame_int16) -> None:
        """Called from the mic callback (real-time thread). Must stay cheap and
        never block — the frame is copied and dropped if the queue is backed up."""
        if not self._running:
            return
        try:
            # frame_int16 is a numpy int16 array (possibly 2-D mono) — flatten to 1-D
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _loop(self) -> None:
        import numpy as np
        while self._running:
            try:
                frame = self._queue.get()
                if frame is None or not self._running:
                    break
                scores = self._model.predict(np.asarray(frame, dtype=np.int16))
                score = 0.0
                if isinstance(scores, dict):
                    # match the jarvis model regardless of exact key suffix
                    for k, v in scores.items():
                        if "jarvis" in k.lower():
                            score = max(score, float(v))
                    if score == 0.0 and scores:
                        score = max(float(v) for v in scores.values())
                if score > self._peak:
                    self._peak = score
                now = time.monotonic()
                if now - self._last_peak_log >= PEAK_LOG_INTERVAL_S:
                    self._last_peak_log = now
                    if self._peak > 0.0:
                        self._logger(f"Wake word: peak {self._peak:.2f} "
                                     f"(needs {self._threshold:.2f})")
                    self._peak = 0.0
                if score >= self._threshold:
                    # drain any backlog so we don't double-fire on the same utterance
                    self._drain()
                    self._logger(f"Wake word: heard (score {score:.2f} "
                                 f"≥ {self._threshold:.2f})")
                    try:
                        self._on_detect()
                    except Exception as e:
                        self._logger(f"Wake word: on_detect error — {e}")
            except Exception as e:
                self._logger(f"Wake word: inference error — {e}")

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
