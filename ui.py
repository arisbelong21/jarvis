from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
import importlib.util
import urllib.request
import webbrowser
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

# Qt's video backend prints the ffmpeg stream banner — codec, bitrate, the
# whole signed googlevideo URL — to the console for every stream it opens. That
# is two screenfuls per video, it looks like an error to anyone reading the log,
# and it puts a URL carrying the viewer's IP address into a terminal people
# paste into bug reports. Silenced here, before Qt initialises its logging.
# An explicit setting from the user is left alone.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PyQt6.QtCore import (
    QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPoint, QPropertyAnimation, QRect, QRectF, QSize, QSizeF, Qt, QTimer,
    QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QFontMetrics, QKeySequence, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient, QShortcut,
)
# Video playback for the HUD. Part of PyQt6, so it costs no new dependency —
# but the multimedia plugins are a separate piece of the Qt install and can be
# absent on a stripped-down system, so a failure here disables one feature
# rather than stopping JARVIS from starting.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
    HAVE_VIDEO = True
except Exception as _e:            # noqa: BLE001 - reported, never fatal
    QAudioOutput = QMediaPlayer = QGraphicsVideoItem = None
    HAVE_VIDEO = False
    print(f"[Video] playback unavailable ({_e}) — the HUD will not show video.")

from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QGraphicsScene, QGraphicsView,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None


def _safe_later(ms: int, fn) -> None:
    """Run `fn` after `ms`, unless the widget it touches has been deleted.

    A pending QTimer keeps its Python closure alive; when a panel rebuilds
    (deleting a provider, refreshing a list) Qt destroys the C++ objects
    behind those widgets, and the callback then raises
    "wrapped C/C++ object ... has been deleted". Unhandled, that ends the
    whole process — which is exactly what deleting a provider used to do.
    The callback is the only thing that knows what it touches, so the guard
    lives here.
    """
    def _run():
        try:
            fn()
        except RuntimeError:
            pass          # the widget is gone — nothing left to restore

    QTimer.singleShot(ms, _run)


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = "ARIS"
APP_PROTOCOL = APP_VERSION.split()[-1]

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
# Grid columns of the JARVIS Home mockup: 230px rail | fluid stage | 340px side.
_LEFT_W  = 230
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


def _ui_font(size: int, bold: bool = False) -> QFont:
    """A monospace face that stays legible at small sizes.

    Consolas first — it was drawn for screens and its strokes hold up at
    10–11px where Courier New turns to fog — with Courier New behind it so
    macOS and Linux fall back to something known instead of an arbitrary
    substitute. Used for the drawers and the header badge; the rest of the
    HUD keeps its Courier New terminal look."""
    f = QFont()
    f.setFamilies(["Consolas", "Courier New"])
    f.setPointSize(size)
    f.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    return f


_DISPLAY_STACK = ["Chakra Petch", "Consolas", "Courier New"]


def _display_font(size: int, bold: bool = False) -> QFont:
    """The mockup's display face (Chakra Petch 700) with safe fallbacks.

    The mockup loads it from Google Fonts; a desktop app should not need the
    network for a letterform, so the family is used only when the OS already
    has it installed and falls back through the same stack _ui_font uses.
    """
    f = QFont()
    f.setFamilies(list(_DISPLAY_STACK))
    f.setPointSize(size)
    f.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    return f


def _hud_fonts() -> None:
    """Point Qt at the repo's bundled font files, if any, so the mockup's
    Chakra Petch renders without the user installing it. Bundled in `fonts/`
    next to ui.py; a missing folder just means the fallbacks keep running."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    if not os.path.isdir(base):
        return
    from PyQt6.QtGui import QFontDatabase
    for name in os.listdir(base):
        if name.lower().endswith((".ttf", ".otf")):
            try:
                QFontDatabase.addApplicationFont(os.path.join(base, name))
            except Exception:
                pass


class C:
    """Theme palette — values from the JARVIS Home design reference.

    The mockup's own numbers (mockup3/Main.dc.html): bg radial-gradient
    #0b2347 → #040b18 → #020610; glass panels linear-gradient
    rgba(14,40,82,.55) → rgba(6,16,34,.7) with a rgba(63,216,255,.22) border
    and inner glow; accent pair #3FD8FF (cyan) + #FFB020 (amber); text
    #d6ecff / #6f8bab / #9fe9ff. Status colours keep their old jobs (ACC/GREEN/
    RED stay fixed), and so do the TEXT colours — an accent must never make
    body text unreadable; everything else hue-linked re-derives from PRI.
    """
    BG        = "#020610"   # mockup body bottom stop #020610
    PANEL     = "#061022"   # glass panel lower stop rgba(6,16,34,.7) flattened
    PANEL2    = "#0a1a38"   # glass panel upper stop rgba(14,40,82,.55) flattened
    BORDER    = "#0e2a4a"   # hairline inside panels (mockup .ev divider ~.1 alpha)
    BORDER_B  = "#1f4a6e"   # stronger border — mockup border rgba(63,216,255,.22) on dark
    BORDER_A  = "#173a5e"   # mid border
    PRI       = "#3fd8ff"   # mockup primary cyan #3FD8FF
    PRI_DIM   = "#2a8aa8"   # dimmed cyan (was #007a99 — lifted to mockup luminance)
    PRI_GHO   = "#0a2030"   # cyan ghost wash — mockup rgba(63,216,255,.07) chips
    ACC       = "#ffb020"   # mockup amber #FFB020 (was orange #ff6b00)
    ACC2      = "#ffd166"   # softer amber for gradients' second stop
    GREEN     = "#3cf0a0"   # mockup success green #3CF0A0
    GREEN_D   = "#1f9a60"   # dimmed green
    RED       = "#ff5a4d"   # mockup danger red #FF5A4D
    MUTED_C   = "#ff5a4d"   # muted-state red — same family as mockup danger
    TEXT      = "#d6ecff"   # mockup body text #d6ecff
    TEXT_DIM  = "#6f8bab"   # mockup .m muted #6f8bab
    TEXT_MED  = "#9fe9ff"   # mockup .chip cyan text #9fe9ff
    WHITE     = "#ffffff"   # mockup brand/headline white
    DARK      = "#030c1c"   # header/entry field — mockup rgba(3,12,28,.8) flattened
    BAR_BG    = "#0e2036"   # mockup .bar track #0e2036


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
# Body text NEVER follows the accent hue. Hue-shifting it turned #d6ecff into
# cream/salmon the moment a warm accent was picked — the memory panel went
# unreadable (reported by the boss, verified from a screenshot: text #ffb39f
# on near-black). The accent belongs on borders, chips and highlights; text
# stays its readable blue-white for every accent. See apply_ui_accent.
_TEXT_KEYS = ("TEXT", "TEXT_DIM", "TEXT_MED")
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        if key in _TEXT_KEYS:
            # Pinned: body text keeps its readable blue-white whatever the
            # accent is. Retheme still sees the key (it is in _HUE_LINKED),
            # so a live accent change also rewrites already-salmon text in
            # existing stylesheets back to the default — no restart needed.
            setattr(C, key, hex0)
            continue
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        # The status panel's extra rows. Cheap psutil reads, same cycle.
        self.disk     = 0.0
        self.net_up   = 0.0
        self.net_down = 0.0
        self.uptime   = 0.0
        self.procs    = 0
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp
            # Extra rows for the system-status panel. Wrapped so a platform
            # quirk in one of them cannot blank the rest of the sample.
            try:
                self.net_up   = sent / 1024 if dt > 0 else 0.0
                self.net_down = recv / 1024 if dt > 0 else 0.0
            except Exception:
                self.net_up = self.net_down = 0.0
            try:
                self.disk = psutil.disk_usage(os.path.abspath(os.sep)).percent
            except Exception:
                self.disk = 0.0
            try:
                self.uptime = max(0.0, time.time() - psutil.boot_time())
            except Exception:
                self.uptime = 0.0
            try:
                self.procs = len(psutil.pids())
            except Exception:
                self.procs = 0

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    # The package prints its own "deprecated, use nvidia-ml-py"
                    # FutureWarning on import. The module it provides is still
                    # the one this app calls, so the notice is noise in a
                    # console that is otherwise used for real diagnostics.
                    import warnings as _warnings
                    with _warnings.catch_warnings():
                        _warnings.simplefilter("ignore", FutureWarning)
                        import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
                "disk": self.disk,
                "net_up": self.net_up,
                "net_down": self.net_down,
                "uptime": self.uptime,
                "procs": self.procs,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self.dimmed   = False     # Ambient/focus mode: dim the glow work
        self._assistant_name = assistant_name

        # The holographic head that fills the HUD. If it could not be imported
        # we fall back to the old glowing core so the panel is never empty.
        self._avatar = None
        if HoloAvatar is not None:
            try:
                self._avatar = HoloAvatar()
            except Exception:
                self._avatar = None

        # Which centrepiece to draw. Read once here and changed live by the
        # settings toggle; the avatar object is kept either way so switching
        # back is instant and costs no reload.
        try:
            from memory.config_manager import get_hud_style
            self.hud_style = get_hud_style()
        except Exception:
            self.hud_style = "face"
        self._core_phase = 0.0
        self.axis_shift = 0.0     # paint-axis alignment with the command row
        # Animations can be switched off in Settings (hud_animations). When
        # off, the stepping timer stops: the centrepiece freezes on its
        # current frame, the paint throttles to events-only, and the looping
        # CPU cost drops to zero. State changes still repaint once each.
        try:
            from memory.config_manager import get_animations_enabled
            self.animations = get_animations_enabled()
        except Exception:
            self.animations = True

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._step_t     = time.time()
        self._blink      = True
        self._blink_tick = 0

        # Rescaled-face cache: the smooth rescale is expensive, so we keep the
        # last result and only rebuild it when the (quantised) size changes.

        # Static grid-dot layer, pre-rendered once per size/theme into a pixmap
        # so paintEvent blits it in one call instead of thousands of drawPoint()s.
        self._grid_cache: QPixmap | None = None
        self._grid_key = None
        # Repaint throttle counter (idle frames drop to ~20 Hz — see _step()).
        self._paint_tick = 0

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        # (frames, start_time, hop) posted by the playback thread — see
        # push_visemes(). None means "no schedule; use the plain level".
        self._visemes = None
        self._vis_i = None        # first schedule frame not yet handed to the mouth
        self._base_scale = 1.0    # slow "breathing" target; amp is added per-frame
        self._base_halo  = 55.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: hand over a schedule of (level, openness, width) frames.

        The playback thread writes up to 200 ms of audio in one go, so a single
        averaged level would only move the mouth five times a second — enough to
        flap, nowhere near enough to articulate. It instead posts the whole
        slice's worth of 20 ms frames here and `_step()` plays them out against
        the wall clock, in step with the audio going to the speakers.

        `at` is the wall-clock time this batch will *begin to sound*, which the
        caller tracks as a playback cursor. It is not the time of the call, and
        the difference is the whole point: `stream.write` returns once the buffer
        accepts the samples, so consecutive batches are handed over far faster
        than they play. Anchoring each one to "now" made every batch start while
        its predecessor was still sounding, so each schedule replaced the last
        after a couple of frames and the mouth only ever played the opening
        instant of every 200 ms — the reason it did not match the words.

        Successive batches are therefore *appended* into one continuous
        timeline, not swapped in. A paragraph is one schedule; the mouth stops
        falling into a gap at every chunk boundary and having to climb back out.
        """
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    # Where in the existing timeline does this batch land?
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        # Continues (or slightly overlaps) what is already
                        # queued: extend rather than restart. Drop whatever has
                        # already been played so the list cannot grow without
                        # bound over a long reply.
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    # ── wajah 2.5D (ui_face25d) ─────────────────────────────────────────
    def _ensure_face25d(self):
        """Buat widget wajah sekali saja; None bila tidak tersedia."""
        if getattr(self, "_face25d", None) is not None:
            return self._face25d
        if getattr(self, "_face25d_tried", False):
            return None
        self._face25d_tried = True
        try:
            from ui_face25d import Face25DWidget
            w = Face25DWidget(self, transparent=True)
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            w.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            w.hide()
            self._face25d = w
            print("[HUD] wajah 2.5D aktif")
        except Exception as e:                   # noqa: BLE001
            print(f"[HUD] wajah 2.5D tidak tersedia: {e}")
            self._face25d = None
        return self._face25d

    def _sync_face25d(self) -> bool:
        """Tampilkan wajah 2.5D bila gaya HUD 'face'.

        False = pakai cara lama (gambar QPainter), sehingga HUD tetap hidup
        walau aset atau Qt gagal memuat widget.
        """
        if getattr(self, "hud_style", "face") != "face25d":
            w = getattr(self, "_face25d", None)
            if w is not None and w.isVisible():
                w.hide()
            return False
        w = self._ensure_face25d()
        if w is None:
            return False
        try:
            # Selaraskan dengan sumbu gambar HUD: HudCanvas memakai
            # `axis_shift` supaya kepala/orbit/teks berada di satu garis
            # dengan baris perintah di bawahnya. Widget ini dulu memakai
            # tengah mentah, jadi wajahnya meleset ke kanan.
            side = int(max(240, min(self.width(), self.height()) * 0.80))
            cx = self.width() / 2 + float(getattr(self, "axis_shift", 0.0) or 0.0)
            cy = self.height() / 2
            geo = (int(cx - side / 2), int(cy - side / 2), side, side)
            if getattr(self, "_f25_geo", None) != geo:
                self._f25_geo = geo
                w.setGeometry(*geo)
            if not w.isVisible():
                w.show()
                w.raise_()
            # status & level suara diteruskan tiap frame: murah dan selalu benar
            w.set_state(getattr(self, "state", "IDLE") or "IDLE")
            w.set_audio_level(float(getattr(self, "_live_amp", 0.0) or 0.0))
        except Exception as e:                   # noqa: BLE001
            print(f"[HUD] wajah 2.5D gagal: {e}")
            try:
                w.hide()
            except Exception:
                pass
            return False
        return True

    def showEvent(self, ev) -> None:
        # Buat widget wajah di luar paintEvent: membuat QWidget saat
        # menggambar bisa memicu re-entrancy pada Qt.
        super().showEvent(ev)
        self._ensure_face25d()

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _paint_atoms(self, p, W: int, H: int, cx: float, cy: float) -> None:
        """Kisi atom 3D yang berputar lambat di belakang kepala.

        Bentuknya dibangkitkan sekali (Fibonacci sphere + dua tetangga
        terdekat per node) supaya biayanya nol per frame selain rotasi dan
        proyeksi. Rotasinya 0,2 rad/s — cukup untuk terasa hidup, jauh di
        bawah ambang yang bikin pusing. Kedalaman diwujudkan lewat ukuran,
        alpha dan panjang garis, bukan warna berbeda: satu warna aksen saja
        supaya tidak ramai.
        """
        if not getattr(self, "animations", True):
            return
        if getattr(self, "_atom_pts", None) is None:
            n = 12
            pts: list[tuple[float, float, float]] = []
            golden = math.pi * (1.0 + 5.0 ** 0.5)
            for i in range(n):
                y = 1.0 - (i / (n - 1)) * 2.0
                rad = math.sqrt(max(0.0, 1.0 - y * y))
                th = golden * i
                pts.append((math.cos(th) * rad, y, math.sin(th) * rad))
            edges: set[tuple[int, int]] = set()
            for i, a in enumerate(pts):
                near = sorted(
                    (j for j in range(n) if j != i),
                    key=lambda j: sum((a[k] - pts[j][k]) ** 2 for k in range(3)),
                )[:3]
                for j in near:
                    edges.add((min(i, j), max(i, j)))
            self._atom_pts = pts
            self._atom_edges = sorted(edges)

        pts, edges = self._atom_pts, self._atom_edges
        t = getattr(self, "_core_phase", 0.0) * 0.20
        # Radius must sit just OUTSIDE the head (the head spans ~0.355·fw),
        # otherwise the lattice hides behind it and reads as noise.
        R = min(W, H) * 0.46
        cyaw, syaw = math.cos(t), math.sin(t)
        cpitch, spitch = math.cos(t * 0.42), math.sin(t * 0.42)

        proj: list[tuple[float, float, float, float]] = []
        for x, y, z in pts:
            x1 = x * cyaw + z * syaw
            z1 = -x * syaw + z * cyaw
            y1 = y * cpitch - z1 * spitch
            z2 = y * spitch + z1 * cpitch
            persp = 1.0 / (1.0 + z2 * 0.42)
            # Datar sedikit secara vertikal (0.82) supaya node paling atas dan
            # paling bawah tidak menabrak judul/status di dalam HUD.
            proj.append((cx + x1 * R * persp,
                         cy + y1 * R * persp * 0.82, z2, persp))

        # Garis dulu — node menimpa ujungnya, jadi sambungan terlihat bersih.
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i, j in edges:
            x1, y1, z1, p1 = proj[i]
            x2, y2, z2, p2 = proj[j]
            depth = (z1 + z2) * 0.5
            a = 0.30 * (1.0 - min(1.0, abs(depth))) * (0.55 + 0.45 * (p1 + p2) * 0.5)
            col = QColor(C.PRI)
            col.setAlphaF(max(0.05, min(0.30, a)))
            pen = QPen(col)
            pen.setWidthF(1.0)
            p.setPen(pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        for x, y, z, persp in proj:
            r = 1.4 + 2.2 * persp
            a = 0.14 + 0.34 * max(0.0, (1.0 - z) * 0.5)
            col = QColor(C.PRI)
            col.setAlphaF(min(0.52, max(0.10, a)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(x, y), r, r)

    def _paint_dust(self, p, W: int, H: int) -> None:
        """Bintik merah bergerak: debu ruang angkasa di belakang HUD.

        Posisi disimpan 0..1 (bukan piksel) supaya tidak perlu dibuat ulang
        saat jendela diubah ukurannya. Bergerak naik perlahan dan berkelip
        dengan fase berbeda-beda; kecepatan memakai selisih waktu nyata, jadi
        gerakannya sama di layar 30 Hz maupun 144 Hz.
        """
        import random

        stars = getattr(self, "_dust", None)
        if stars is None:
            stars = [[random.random(), random.random(),
                      random.uniform(0.0006, 0.0028), random.uniform(0.7, 2.2),
                      random.uniform(0.0, 6.283)] for _ in range(170)]
            self._dust = stars

        # Kesalahan di sini TIDAK boleh mematikan aplikasi: PyQt6 membatalkan
        # proses bila paintEvent melempar exception (kejadian 25 Okt: exit 127
        # tanpa pesan sama sekali). Debu latar adalah hiasan — lebih baik
        # dilewati daripada mematikan JARVIS.
        try:
            self._paint_dust_body(p, stars, W, H)
        except Exception as e:                   # noqa: BLE001
            print(f"[HUD] debu latar dilewati: {e}")

    def _paint_dust_body(self, p, stars, W: int, H: int) -> None:
        now = time.monotonic()
        last = getattr(self, "_dust_last", now)
        dt = min(0.12, max(0.0, now - last))
        self._dust_last = now

        # save/restore: kode setelah ini (atom, orbit, kepala) memakai pen &
        # brush sendiri; meninggalkan state aneh di sini membuat lukisan
        # berikutnya tidak terdefinisi.
        # fillRect dipakai (bukan drawEllipse + QBrush) karena tidak menyentuh
        # pen/brush sama sekali — paling aman di dalam paintEvent HUD.
        p.save()
        for s in stars:
            s[1] -= s[2] * dt * 60.0
            if s[1] < -0.02:
                s[1] = 1.02
                s[0] = random.random()
            twinkle = 0.55 + 0.45 * math.sin(now * 1.6 + s[4])
            alpha = int(185 * twinkle * (0.45 + 0.55 * (s[3] / 2.2)))
            if alpha <= 2:
                continue
            r = s[3]
            p.fillRect(QRectF(s[0] * W - r, s[1] * H - r, r * 2.0, r * 2.0),
                       QColor(255, 78, 56, min(255, alpha)))
        p.restore()

    def _paint_field_rules(self, p, cx: float, cy: float, fw: float) -> None:
        """Dua garis horizontal tipis sebagai bingkai medan.

        Sengaja hanya garis: cincin dan busur sudah ada di HUD, dan menambah
        bentuk melingkar lagi akan membuat latar terasa padat. Alpha 0,09 —
        cukup untuk membingkai, tidak cukup untuk menarik perhatian dari
        wajah dan status.
        """
        for yy in (cy - fw * 0.36, cy + fw * 0.30):
            rule = QColor(C.PRI)
            rule.setAlphaF(0.09)
            pen = QPen(rule)
            pen.setWidthF(1.0)
            p.setPen(pen)
            p.drawLine(QPointF(cx - fw * 0.47, yy), QPointF(cx + fw * 0.47, yy))

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Pre-render the static grid-dot background into a transparent pixmap so
        paintEvent can blit it once per frame instead of running a nested
        drawPoint() loop across the whole widget every 16 ms."""
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pm)
        gp.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                gp.drawPoint(x, y)
        gp.end()
        return pm

    def _step(self):
        # Animations off (Settings): freeze the loop. One repaint lands the
        # current frame, then nothing moves until the flag flips back or the
        # speaking/listening state itself changes (state changes call update()
        # through their own paths, so important feedback still shows).
        if not getattr(self, "animations", True):
            if self._tmr.isActive():
                self._tmr.stop()
            return
        if not self._tmr.isActive():
            self._tmr.start(16)
        self._tick += 1
        now = time.time()

        # ── Live audio reactivity ────────────────────────────────────────────
        # A viseme schedule, if one is playing, gives both the level and the
        # mouth shape for this exact instant; otherwise fall back to the peak
        # level the audio threads pushed in.
        v_open = v_wide = v_level = None
        v_seq = None
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                # Hand over *every* frame since the last tick, not just the one
                # under the cursor. This timer runs at 60 Hz but the paint is
                # throttled and the machine may be busy, so a tick can span two
                # or three 20 ms frames — and a consonant closure is only two
                # frames long. Sampling one and discarding the rest is how the
                # closures between words went missing.
                j = self._vis_i if self._vis_i is not None else i
                v_seq = frames[max(0, j):i + 1]
                self._vis_i = max(j, i + 1)
                v_level, v_open, v_wide = frames[i]
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None        # schedule spent
                self._vis_i = None

        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # The avatar animates off the very same smoothed level the waveform
        # uses — one audio source, so the mouth can never drift out of sync.
        dt = now - self._step_t
        self._step_t = now
        # Integrated, not derived from absolute time: multiplying wall-clock by
        # a rate that changes with state jumps the rings the instant JARVIS
        # starts talking. Same lesson the head's sway taught.
        self._core_phase += min(0.10, max(0.0, dt))

        if self._avatar is not None and self.hud_style == "face":
            self._avatar.step(dt, amp, speaking=self.speaking,
                              muted=self.muted, state=self.state,
                              v_open=v_open, v_wide=v_wide or 0.0,
                              v_level=v_level, v_seq=v_seq,
                              v_hop=(sched[2] if sched is not None else 0.02))
        else:
            # Fallback core: slow "breathing" base target, lifted by the level.
            if now - self._last_t > (0.12 if self.speaking else 0.5):
                if self.speaking:
                    self._base_scale = 1.03
                    self._base_halo  = 122.0
                elif self.muted:
                    self._base_scale = random.uniform(0.998, 1.002)
                    self._base_halo  = random.uniform(15, 28)
                else:
                    self._base_scale = random.uniform(1.001, 1.008)
                    self._base_halo  = random.uniform(48, 68)
                self._last_t = now

            if self.muted:
                self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
            elif self.speaking:
                self._tgt_scale = self._base_scale + amp * 0.13
                self._tgt_halo  = self._base_halo  + amp * 95.0
            else:
                self._tgt_scale = self._base_scale + amp * 0.06
                self._tgt_halo  = self._base_halo  + amp * 75.0

            sp = 0.38 if self.speaking else (0.30 if amp > 0.02 else 0.15)
            self._scale += (self._tgt_scale - self._scale) * sp
            self._halo  += (self._tgt_halo  - self._halo)  * sp

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
            _blinked = True
        else:
            _blinked = False

        # Repaint throttling — advancing the animation state above is cheap at
        # 60 Hz, but the paint is heavy. Active (speaking, audio, thinking) runs
        # at ~30 Hz, which is the frame rate animation has used for talking
        # characters forever and is indistinguishable here; idle drops to ~20 Hz
        # so a sleeping HUD stops pinning a CPU core. The visuals stay smooth
        # either way because the animation state keeps stepping at 60 Hz.
        self._paint_tick = (self._paint_tick + 1) % 6
        active = (self.speaking or amp > 0.02
                  or self.state in ("THINKING", "PROCESSING"))
        if _blinked or (self._paint_tick % 2 == 0 if active
                        else self._paint_tick % 3 == 0):
            # Nothing is on screen when the window is hidden or minimised, so
            # rendering the avatar into it is pure waste — and this app is meant
            # to sit running all day. The animation state above keeps stepping,
            # so it picks up mid-motion instead of snapping when you come back.
            if self._on_screen():
                self.update()

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True      # never let a visibility check stop the HUD drawing

    # ── reactor core ─────────────────────────────────────────────────────────
    # The centrepiece for anyone who did not want a face looking back at them.
    # Built from the same budget as the head — software QPainter, no GPU — and
    # from the same principle: everything on it means something. The rings turn
    # at a rate the state sets, the spectrum ring is the real audio level, and
    # the core brightens with the voice. Nothing here is decoration that moves
    # for its own sake, which is what made the old glowing orb feel dead.

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_orbit(self, p: QPainter, cx: float, cy: float, r: float,
                     W: float = 0.0, H: float = 0.0):
        """The mockup's third centrepiece: a wireframe head inside four orbit
        rings, standing on a glowing floor disc.

        Geometry lifted from mockup3 .core (340px box): .r1 solid rim at inset
        0, .r2 dashed ring at 16, .r3 amber arc pair spinning 7s, .r4 cyan arc
        pair counter-spinning 11s; the head fills the middle 170/340 of the
        box; .floor is the wide elliptical glow at the base. Timebase is
        `_core_phase`, the same integrated clock that drives the reactor, so
        it inherits the state speeds (idle drift, thinking hurry) and every
        colour follows the live theme.
        """
        main, acc = self._core_colours()
        bg = qcol(C.BG)
        acc = qcol(acc) if isinstance(acc, str) else acc
        amp = self._amp_disp
        t = self._core_phase

        def blend(col, a: float) -> QColor:
            col = qcol(col) if isinstance(col, str) else col
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red() + (col.red() - bg.red()) * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue() + (col.blue() - bg.blue()) * k))

        p.setBrush(Qt.BrushStyle.NoBrush)

        # r1 — the solid rim (mockup: 1px rgba(63,216,255,.3)).
        p.setPen(QPen(blend(main, 0.55), 1.2))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # r2 — the dashed ring (mockup: dashed, spinning 60s → slow drift).
        pen = QPen(blend(main, 0.70), 1.2)
        pen.setDashPattern([4.0, 3.0])
        pen.setDashOffset(t * 12.0)
        p.setPen(pen)
        r2 = r * (340.0 - 32.0) / 340.0
        p.drawEllipse(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2))

        # r3 — two amber arcs, 7s spin. Mockup border-top+right amber.
        rate_t = t * (28.0 + (36.0 if self.state in ("THINKING", "PROCESSING")
                              else 0.0))
        r3 = r * (340.0 - 68.0) / 340.0
        p.setPen(QPen(blend(C.ACC, 0.95), 2.6))
        box3 = QRectF(cx - r3, cy - r3, r3 * 2, r3 * 2)
        p.drawArc(box3, int((rate_t % 360.0) * 16), 120 * 16)
        p.drawArc(box3, int(((rate_t + 180.0) % 360.0) * 16), 120 * 16)

        # r4 — two cyan arcs, counter-spinning 11s.
        r4 = r * (340.0 - 116.0) / 340.0
        p.setPen(QPen(blend(main, 0.9), 2.0))
        box4 = QRectF(cx - r4, cy - r4, r4 * 2, r4 * 2)
        p.drawArc(box4, int((-rate_t * 0.64 % 360.0) * 16), 100 * 16)
        p.drawArc(box4, int(((-rate_t * 0.64 + 180.0) % 360.0) * 16), 100 * 16)

        # floor — the glowing ellipse the figure stands on (.floor).
        fw_, fh_ = r * 0.706, r * 0.12          # 240/340 x 40/340
        fy = cy + r * 0.86
        fg = QRadialGradient(cx, fy, fw_)
        fg.setColorAt(0.0, blend(main, 0.30 + 0.25 * amp))
        fg.setColorAt(0.55, blend(C.ACC, 0.10))
        fg.setColorAt(1.0, blend(main, 0.0))
        p.setBrush(QBrush(fg))
        p.setPen(QPen(blend(C.ACC, 0.55), 1.6))
        p.drawEllipse(QRectF(cx - fw_, fy - fh_ / 2, fw_ * 2, fh_))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # the wireframe head (mockup .head, 170x204 in a 340px box → 0.5, 0.6).
        # Mockup geometry: the head spans x 32..168 inside the 340px box, so
        # its own centre is x=100 — 70 px left of the box centre (170).
        # Anchoring X(100) on cx is what puts the HEAD, not its bounding box,
        # exactly on the rings' centre.
        hw, hh = r * 0.98, r * 1.18
        hx = cx - 100.0 * (hw / 170.0)
        # Vertical: head spans y 10..210 → mid 110. Solving Y(110) == cy
        # (float bob added below) puts the head's optical centre on the
        # rings' centre too, not just its horizontal position.
        oy = cy - (110.0 - 10.0) * (hw / 170.0) * (204.0 / 240.0)
        # gentle float (mockup float 5s, ±6px) + voice bob
        oy -= (math.sin(t * 1.25) * 0.012 + amp * 0.05) * r

        S = hw / 170.0     # scale: mockup head coords are 0..170 wide

        def X(v): return hx + v * S
        def Y(v): return oy + (v - 10) * S * (204.0 / 240.0)

        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # outer silhouette (fill translucent like the mockup, then stroke)
        sil = QPolygonF([QPointF(X(100), Y(10)), QPointF(X(145), Y(25)),
                         QPointF(X(165), Y(65)), QPointF(X(168), Y(110)),
                         QPointF(X(158), Y(150)), QPointF(X(140), Y(185)),
                         QPointF(X(118), Y(205)), QPointF(X(100), Y(210)),
                         QPointF(X(82), Y(205)), QPointF(X(60), Y(185)),
                         QPointF(X(42), Y(150)), QPointF(X(32), Y(110)),
                         QPointF(X(35), Y(65)), QPointF(X(55), Y(25))])
        fill = QColor(int(bg.red() + (main.red() - bg.red()) * 0.14),
                      int(bg.green() + (main.green() - bg.green()) * 0.14),
                      int(bg.blue() + (main.blue() - bg.blue()) * 0.14))
        p.setBrush(QBrush(fill))
        p.setPen(QPen(blend(main, 0.95), 1.6))
        p.drawPolygon(sil)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # inner wires (the mockup's second path — faceted cranium lines)
        wires = [
            ((55, 25), (100, 50), (145, 25)),
            ((35, 65), (100, 50), (165, 65)),
            ((35, 65), (70, 90)), ((165, 65), (130, 90)),
            ((70, 90), (100, 50), (130, 90)),
            ((32, 110), (70, 90), (100, 105)),
            ((100, 105), (130, 90), (168, 110)),
            ((70, 90), (75, 120)), ((75, 120), (100, 105), (125, 120)),
            ((125, 120), (130, 90)),
            ((100, 105), (90, 145), (110, 145), (100, 105)),
            ((75, 120), (90, 145), (60, 150), (75, 120)),
            ((125, 120), (110, 145), (140, 150), (125, 120)),
            ((42, 150), (60, 150)), ((60, 150), (32, 110)),
            ((158, 150), (140, 150)), ((140, 150), (168, 110)),
            ((90, 145), (82, 170), (118, 170), (110, 145)),
            ((60, 185), (82, 170), (100, 190), (118, 170), (140, 185)),
        ]
        p.setPen(QPen(blend(main, 0.55), 1))
        for poly in wires:
            pts = [QPointF(X(v), Y(w)) for v, w in poly]
            for a, b in zip(pts, pts[1:]):
                p.drawLine(QLineF(a, b))

        # the eyes — glowing, blinking (mockup .eye: 4s blink cycle)
        eye_on = (self._blink_tick < 34)
        ec = blend(qcol(C.GREEN) if not self.muted else qcol(C.MUTED_C),
                   0.95 if eye_on else 0.15)
        p.setPen(QPen(ec, 1))
        p.setBrush(QBrush(ec))
        for ex in (76, 124):
            p.drawEllipse(QPointF(X(ex), Y(102)), 9 * S, 3.5 * S)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # the scanline sweep (.sc: 140x2 sweeping top→bottom, 3.2s)
        sy_ = oy + ((t * 0.31) % 1.0) * 200 * S * (204.0 / 240.0)
        p.fillRect(QRectF(X(30), sy_, 140 * S, 1.6),
                   blend(qcol(C.WHITE), 0.55))

    def _paint_core(self, p: QPainter, cx: float, cy: float, r: float,
                    W: float = 0.0, H: float = 0.0):
        """Draw the reactor at (cx, cy) with outer radius r, using the whole
        canvas (W x H) for the marks that frame it."""
        main, acc = self._core_colours()
        bg = qcol(C.BG)
        amp = self._amp_disp
        t = self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted

        def blend(col: QColor, a: float) -> QColor:
            """Pre-mix onto the background instead of asking Qt to composite.
            The raster engine's opaque path is several times faster than its
            translucent one, and everything here is a line or an arc."""
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue()  + (col.blue()  - bg.blue())  * k))

        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. The atmosphere. One radial gradient doing what a stack of discs did
        #    badly: a wide, soft body of light that gives the thing presence
        #    before any detail is read. This single element decides whether the
        #    HUD looks vast or looks small, so it is drawn first and drawn big.
        # Concentrated rather than spread: a gradient reaching the outer rim
        # washes the whole disc a flat dim blue and reads as fog. Ending it at
        # two thirds leaves it a body of light with somewhere to fall off to,
        # which is what makes it look lit rather than tinted.
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0, a1 in ((r * 0.70, 0.30, 0.0), (r * 0.34, 0.34, 0.0)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
            g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
            g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(main, a1))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 2. Frame marks at the corners of the whole canvas, not of the circle.
        #    They are what set the scale: the eye reads the reactor as filling
        #    the room rather than sitting in the middle of it.
        if W > 40 and H > 40:
            m, arm = min(W, H) * 0.035, min(W, H) * 0.055
            p.setPen(QPen(blend(main, 0.45), 1.4))
            for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + sx * (W / 2 - m)
                y = cy + sy * (H / 2 - m)
                p.drawLine(QLineF(x, y, x - sx * arm, y))
                p.drawLine(QLineF(x, y, x, y - sy * arm))

        # 3. Crosshair across the full canvas, broken around the core so it
        #    frames the reactor rather than crossing it.
        p.setPen(QPen(blend(main, 0.16), 1))
        gap = r * 0.62
        if W > 40:
            p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
            p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
        if H > 40:
            p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
            p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))

        # 4. Two thin outer circles. Sparse on purpose — a dense ring reads as a
        #    grey band at this size, and restraint is what made the original
        #    look expensive.
        for rr, a in ((1.00, 0.34), (0.93, 0.16)):
            rad = r * rr
            p.setPen(QPen(blend(main, a), 1))
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # 5. Long, sparse graduations: 24 majors reaching well in from the rim,
        #    with shorter minors between them.
        major, minor = [], []
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            if i % 3 == 0:
                major.append(QLineF(cx + ca * r * 0.885, cy + sa * r * 0.885,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
            else:
                minor.append(QLineF(cx + ca * r * 0.945, cy + sa * r * 0.945,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
        p.setPen(QPen(blend(main, 0.42), 1.3))
        p.drawLines(major)
        p.setPen(QPen(blend(main, 0.18), 1))
        p.drawLines(minor)

        # 6. Sweeping arcs. Long spans, not dashes — the original's grandeur
        #    came from a few big strokes. Speed is the state: idle drifts,
        #    thinking hurries, speaking runs.
        rate = 1.0 + (1.9 if self.state in ("THINKING", "PROCESSING") else 0.0) \
                   + (1.2 if self.speaking else 0.0)
        for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                (0.955, 118, 2, +1, acc,  0.75, 2.0),
                (0.845, 82,  3, -1, main, 0.38, 1.3),
                (0.760, 150, 1, +1, acc,  0.45, 1.6),
                (0.660, 64,  4, -1, main, 0.26, 1.1),
                (0.545, 128, 2, +1, main, 0.30, 1.2))):
            rad = r * rr
            p.setPen(QPen(blend(col, a), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate * (9 + k * 6) * dirn) % 360.0
            for sgm in range(count):
                p.drawArc(box, int((base + sgm * (360.0 / count)) * 16),
                          int(span * 16))

        # 7. The voice, as a ring of graduations that grow with it. Kept out at
        #    a wide radius so it never crowds the middle.
        n = 60
        ring = r * 0.415
        spikes = []
        for i in range(n):
            a = math.radians(i * (360.0 / n))
            ca, sa = math.cos(a), math.sin(a)
            wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
            idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
            h = r * (idle + (amp * 0.20 * wob if live else 0.0))
            spikes.append(QLineF(cx + ca * ring, cy + sa * ring,
                                 cx + ca * (ring + h), cy + sa * (ring + h)))
        p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.6))
        p.drawLines(spikes)

        # 8. The inner ring the name sits in.
        inner = r * 0.355
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))

        # 9. The name, sized from the string rather than from the radius alone:
        #    "J.A.R.V.I.S" and a name someone renamed to "MAX" are very
        #    different widths, and a fixed fraction of r spills one of them past
        #    the ring it is supposed to sit inside.
        name = self._assistant_name or ""
        if name:
            space = max(1.0, r * 0.018)
            fsz = max(8, int(min(r * 0.105,
                                 (inner * 1.75) / max(1, len(name)) * 1.6 - space)))
            f = QFont("Courier New", fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                       Qt.AlignmentFlag.AlignCenter, name)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():      # device not ready (e.g. 0-size during layout) — skip cleanly
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Latar "luar angkasa" (permintaan bos 25 Okt): hampir hitam dengan
        # sisa merah sangat tipis di tengah — bukan biru malam. Warna biru
        # membuat cincin merah & wajah terasa ditempel di atas panel; hitam
        # membuat keduanya menyatu seperti di ruang hampa.
        _W0, _H0 = self.width(), self.height()
        _bg = QRadialGradient(_W0 * 0.5, _H0 * 0.44, max(_W0, _H0) * 0.86)
        _bg.setColorAt(0.00, qcol("#150405"))
        _bg.setColorAt(0.30, qcol("#0c0203"))
        _bg.setColorAt(0.62, qcol("#050102"))
        _bg.setColorAt(1.00, qcol("#000000"))
        p.fillRect(self.rect(), _bg)
        if not os.environ.get("JARVIS_NO_STARS"):
            self._paint_dust(p, _W0, _H0)

        # A soft horizontal band across the middle, the way the reference
        # lights its centre: transparent at the top and bottom, a thin veil of
        # accent at mid-height. One gradient, no extra shape.
        _band = QLinearGradient(0.0, 0.0, 0.0, float(_H0))
        _clear = qcol("#000000", 0)          # qcol forces alpha=255, so pass 0
        _band.setColorAt(0.00, _clear)
        _band.setColorAt(0.42, qcol("#0d2f4a"))
        _band.setColorAt(0.58, qcol("#0d2f4a"))
        _band.setColorAt(1.00, _clear)
        p.setOpacity(0.35)
        p.fillRect(self.rect(), _band)
        p.setOpacity(1.0)

        # Ambient / focus mode: paint the whole scene, then veil it — one
        # translucent wash is cheaper than threading a dim factor through
        # every colour the HUD mixes.
        if getattr(self, "dimmed", False):
            p.setOpacity(0.45)

        W, H = self.width(), self.height()
        # Paint axis: the command column below (pill + chips) is centred on
        # the WHOLE center-split, while this canvas only spans the area right
        # of the status column — so its raw centre sits ~half a status-column
        # too far right. axis_shift (kept fresh by the window) corrects that,
        # putting head, orbit, status text and waveform on one true axis.
        cx, cy = W / 2 + getattr(self, "axis_shift", 0.0), H / 2
        fw = min(W, H)

        # grid dots — blitted from a cached layer; rebuilt only when the size
        # or the theme's ghost colour changes (so live re-theming still works).
        _gkey = (W, H, C.PRI_GHO)
        if self._grid_cache is None or self._grid_key != _gkey:
            self._grid_cache = self._make_grid(W, H)
            self._grid_key   = _gkey
        p.drawPixmap(0, 0, self._grid_cache)

        # ── starfield ────────────────────────────────────────────────────
        # Ninety slow drifting points behind the head. Deterministic (seeded
        # once), animated by the same phase that drives the avatar, and
        # skipped entirely when animations are switched off — so the "alive"
        # look never costs anything on a machine that asked for stillness.
        if getattr(self, "animations", True):
            if getattr(self, "_stars", None) is None or \
                    getattr(self, "_stars_size", None) != (W, H):
                _rnd = random.Random(7)
                self._stars = [
                    (_rnd.random(), _rnd.random(), _rnd.uniform(0.5, 1.5),
                     _rnd.uniform(0.0, 6.283), _rnd.uniform(0.15, 0.55))
                    for _ in range(90)
                ]
                self._stars_size = (W, H)
            _t = getattr(self, "_core_phase", 0.0)
            p.setPen(Qt.PenStyle.NoPen)
            for _fx, _fy, _r, _ph, _a0 in self._stars:
                _x = _fx * W + 7.0 * math.sin(_t * 0.45 + _ph)
                _y = _fy * H + 5.0 * math.cos(_t * 0.38 + _ph * 1.7)
                _col = QColor(C.PRI)
                _col.setAlphaF(min(1.0, _a0 * (0.55 + 0.45 *
                                               math.sin(_t * 1.1 + _ph))))
                p.setBrush(_col)
                p.drawEllipse(QPointF(_x, _y), _r, _r)

        # Wajah 2.5D membawa cincin orbitnya sendiri. Kisi atom + garis medan
        # lama digambar dengan pusat & skala berbeda, sehingga terlihat miring
        # dan bertabrakan (kejadian 25 Okt: bos menyebutnya "tidak lurus").
        # Jadi keduanya dilewati saat wajah 2.5D aktif.
        _face25 = self._sync_face25d()
        if not _face25:
            self._paint_atoms(p, W, H, cx, cy)

        # ── field rules ─────────────────────────────────────────────────────
        # HUD ini SUDAH punya sistem cincin orbit sendiri (busur berputar di
        # sekeliling inti). Menambah elips baru hanya membuat dobel dan ramai,
        # jadi yang ditambahkan di sini cuma dua garis horizontal tipis —
        # bingkai medan seperti panel TORVIS, tanpa bersaing dengan cincin.
        if not _face25:
            self._paint_field_rules(p, cx, cy, fw)

        # ── holographic head ────────────────────────────────────────────────
        # Sized to the band between the top of the canvas and the status line,
        # capped by width, so it fills the HUD at any window size — including
        # fullscreen — without ever colliding with the status text below.
        _sy_status = cy + fw * 0.40
        if _face25:
            # wajah 2.5D menggambar dirinya sendiri (widget anak di atas HUD)
            pass
        elif self._avatar is not None and self.hud_style == "face":
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r_head = min(fw * 0.355, _band_h / (self._avatar.SPAN + 0.08))
            _head_cy = _band_t + (_band_h - self._avatar.SPAN * _r_head) / 2.0 + _r_head

            if self.muted:
                _main = _acc = qcol(C.MUTED_C)
            else:
                _main = qcol(C.PRI)
                if self.speaking:
                    _acc = qcol(C.ACC)
                elif self.state in ("THINKING", "PROCESSING"):
                    _acc = qcol(C.ACC2)
                elif self.state == "LISTENING":
                    _acc = qcol(C.GREEN)
                else:
                    _acc = qcol(C.PRI)
            self._avatar.paint(p, cx, _head_cy, _r_head, _main, _acc, qcol(C.BG))

        # reactor core — the other centrepiece, and the fallback if the head
        # could not be built. There is no third path: the old face.png branch
        # was unreachable (no such file ships) and the bare orb it fell through
        # to is what this replaces.
        elif self.hud_style == "orbit":
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            # The mockup's core is a 340px box on a 900px-tall board — about
            # 0.62 of the band. Past 0.55 the rings collided with the status
            # line; 0.52 of the band square is the largest clean fit.
            _r = min(W * 0.30, _band_h * 0.34)
            self._paint_orbit(p, cx, _band_t + _band_h * 0.42, _r, W, _band_h)

        else:
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r = min(W * 0.46, _band_h / 2.0)
            self._paint_core(p, cx, _band_t + _band_h / 2.0, _r, W, _band_h)

        # status text
        sy = _sy_status

        # pipeline THINK·PLAN·EXECUTE — the mockup's .pipe, driven by the real
        # state machine, not a decoration: THINKING lights THINK, PROCESSING
        # lights EXECUTE, speaking marks the whole run done. Idle, it is dim.
        stages = ("THINK", "PLAN", "EXECUTE")
        if self.speaking:
            pipe_cols = (C.PRI, C.PRI, C.PRI)          # done — cyan
            active = -1
        elif self.state == "THINKING":
            pipe_cols = (C.ACC, C.TEXT_DIM, C.TEXT_DIM)
            active = 0
        elif self.state == "PROCESSING":
            pipe_cols = (C.PRI, C.ACC, C.ACC)
            active = 2
        else:
            pipe_cols = (C.TEXT_DIM,) * 3
            active = -1
        pf = QFont("Courier New", 9, QFont.Weight.Bold)
        p.setFont(pf)
        fm = p.fontMetrics()
        gap = fm.horizontalAdvance(" · ")
        words = [fm.horizontalAdvance(s) for s in stages]
        total = sum(words) + 2 * gap
        px = cx - total / 2
        py = sy - 30
        for i, (s, wdt) in enumerate(zip(stages, words)):
            p.setPen(QPen(qcol(pipe_cols[i]), 1))
            p.drawText(QRectF(px, py, wdt, 18),
                       Qt.AlignmentFlag.AlignCenter, s)
            px += wdt
            if i < 2:
                p.setPen(QPen(qcol(C.TEXT_DIM), 1))
                p.drawText(QRectF(px, py, gap, 18),
                           Qt.AlignmentFlag.AlignCenter, "·")
                px += gap

        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        # Tracking lebar seperti label instrumen: huruf terpisah membuat teks
        # kecil terbaca sebagai label teknis, bukan kalimat.
        _sf = QFont("Courier New", 11, QFont.Weight.Bold)
        _sf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.6)
        p.setFont(_sf)
        p.drawText(QRectF(cx - W / 2, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform — reacts to the real audio level (mic while listening,
        # JARVIS's own voice while speaking). Falls back to a gentle idle
        # ripple when there's no sound. _amp_disp is the smoothed 0–1 level.
        wy = sy + 30
        N, bw = 36, 8
        wx0 = cx - (N * bw) / 2
        amp = self._amp_disp
        mid = (N - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env     = (1.0 - abs(i - mid) / mid) ** 0.7      # center-weighted hump
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle    = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt     = int(max(2, min(24, idle + amp * 22.0 * env * shimmer)))
                if amp > 0.05:
                    cl = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        p.end()   # end deterministically so the backing store never flushes an active painter

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(46)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 8, 56, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 6, W - 6, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()


class Sparkline(QWidget):
    """A small live trace beside a bar.

    The reference dashboard's SYSTEM HEALTH card shows the same figure two
    ways: where it is right now (the bar) and where it has been (the trace).
    Only the bar existed here, so this adds the second half. It holds the
    last 48 samples of one series and draws them as a filled polyline — fed
    by the same two-second metric timer as every other gauge, so it costs no
    new thread and no new dependency.
    """

    _MAX = 48

    def __init__(self, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._color = color
        self._vals: list[float] = []
        self.setFixedWidth(74)
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    # A percentile series with no change is a flat line, not a new shape —
    # skipping the repaint keeps a quiet machine from redrawing 40 times.
    def push(self, value: float) -> None:
        v = max(0.0, min(100.0, float(value)))
        if self._vals and abs(self._vals[-1] - v) < 0.05:
            return
        self._vals.append(v)
        if len(self._vals) > self._MAX:
            del self._vals[:len(self._vals) - self._MAX]
        self.update()

    def clear(self) -> None:
        self._vals = []
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setPen(QPen(qcol(C.BORDER), 1, Qt.PenStyle.DotLine))
        p.drawLine(0, H - 3, W, H - 3)

        vals = self._vals
        if len(vals) < 2:
            p.end()
            return

        step = W / (self._MAX - 1)
        off = W - step * (len(vals) - 1)
        pts = [QPointF(off + i * step, H - 4 - (H - 9) * (v / 100.0))
               for i, v in enumerate(vals)]

        area = QPainterPath()
        area.moveTo(pts[0])
        for pt in pts[1:]:
            area.lineTo(pt)
        area.lineTo(pts[-1].x(), H - 3)
        area.lineTo(pts[0].x(), H - 3)
        area.closeSubpath()

        fill = qcol(self._color)
        fill.setAlpha(46)
        p.setBrush(QBrush(fill))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(area)

        p.setPen(QPen(qcol(self._color), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF(pts))
        p.end()


class _RingGauge(QWidget):
    """One circular gauge — the reference's SYSTEM STATUS rings.

    A track circle, an arc for the value, the percentage in the middle and
    the caption (label + detail) under it. Fixed size, so three rings plus
    gutters fit the card at any window size.
    """

    def __init__(self, label: str, color: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._detail = "—"
        self.setFixedSize(74, 92)

    def set_value(self, pct: float, detail: str) -> None:
        v = max(0.0, min(100.0, float(pct)))
        if v == self._value and detail == self._detail:
            return
        self._value = v
        self._detail = detail
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = 56
        x = (self.width() - side) / 2
        rect = QRectF(x, 2, side, side)
        pen_w = 5.0

        # track
        p.setPen(QPen(QColor(C.BORDER), pen_w))
        p.drawEllipse(rect.adjusted(pen_w, pen_w, -pen_w, -pen_w))

        # value arc: starts at 12 o'clock, sweeps clockwise by the value.
        arc_rect = rect.adjusted(pen_w, pen_w, -pen_w, -pen_w)
        col = QColor(self._color)
        if self._value >= 85:
            col = QColor(C.RED)
        elif self._value >= 65:
            col = QColor(C.ACC)
        p.setPen(QPen(col, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        span = int(-self._value * 3.6 * 16)
        p.drawArc(arc_rect, 90 * 16, span)

        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        p.drawText(arc_rect, Qt.AlignmentFlag.AlignCenter, f"{int(self._value)}%")

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_MED), 1))
        p.drawText(QRectF(0, side - 2, self.width(), 14),
                   Qt.AlignmentFlag.AlignCenter, self._label)
        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, side + 10, self.width(), 12),
                   Qt.AlignmentFlag.AlignCenter, self._detail)
        p.end()


class NetGraph(QWidget):
    """The little two-line throughput graph under the rings."""

    _MAX = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self._down: list[float] = []
        self._up: list[float] = []
        self.setMinimumHeight(46)

    def push(self, down_kbs: float, up_kbs: float) -> None:
        self._down.append(max(0.0, float(down_kbs)))
        self._up.append(max(0.0, float(up_kbs)))
        if len(self._down) > self._MAX:
            del self._down[:len(self._down) - self._MAX]
            del self._up[:len(self._up) - self._MAX]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        peak = max(max(self._down or [0]), max(self._up or [0]), 1.0) * 1.15

        def series(vals, color):
            if len(vals) < 2:
                return
            path = QPainterPath()
            for i, v in enumerate(vals):
                x = W - (len(vals) - 1 - i) * (W / (self._MAX - 1))
                y = H - (v / peak) * (H - 6) - 2
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(qcol(color), 1.2))
            p.drawPath(path)

        series(self._down, C.PRI)
        series(self._up, C.GREEN)
        p.end()


def _tg_norm_status(status: str) -> str:
    """Status yang sah untuk Live Task Graph: ok | run | wait.

    Apa pun yang masuk dinormalkan di sini, supaya pemanggil dari thread lain
    tidak bisa membuat node dengan status tak dikenal (yang akan tergambar
    sebagai baris kosong tanpa penjelasan).
    """
    s = str(status or "").strip().lower()
    if s in ("ok", "done", "selesai", "sukses"):
        return "ok"
    if s in ("wait", "queued", "antre", "menunggu"):
        return "wait"
    return "run"


class TaskGraph(QWidget):
    """The mockup's 'Live Task Graph' — the tool steps JARVIS is actually
    running, drawn as the mockup's .nd nodes: green=done, amber pulsing=run,
    grey=queued.

    Fed by MainWindow.observe_task() from real session events. Pure paint +
    plain lists: no timer of its own; it repaints when fed and never spins
    idly, and honours the HUD animation flag (the pulse freezes solid).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps: list[dict] = []      # {label, status: ok|run|wait, ms}
        self._pulse = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(120)

    _MAXN = 6

    def _tick(self):
        if self._pulse >= 1.0:
            self._pulse = 0.0
        else:
            self._pulse += 0.12
        # repaint only while something is running (the pulse) or a frame or two
        # after it stopped; a graph of finished steps is a still image.
        if any(s["status"] == "run" for s in self._steps) or self._pulse > 0.15:
            self.update()

    def mark(self, label: str, status: str, ms: float | None = None):
        # Langkah baru yang berjalan berarti langkah sebelumnya selesai —
        # tanpa ini seluruh grafik akan tetap berkedip "run" selamanya.
        if (self._steps and self._steps[-1]["status"] == "run"
                and _tg_norm_status(status) == "run"):
            self._steps[-1]["status"] = "ok"
        self._steps.append({"label": label, "status": status, "ms": ms})
        self._steps = self._steps[-self._MAXN:]
        self.update()

    def clear(self):
        self._steps = []
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), qcol(C.PANEL))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 6, 6)

        t = QLabel("LIVE TASK GRAPH", self)
        t.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
        t.move(8, 4)

        rows = self._steps[-self._MAXN:]
        rh = max(16, (H - 30) // max(1, self._MAXN))
        y = 24
        anim = getattr(self, "_allow_anim", True)
        for i in range(self._MAXN):
            if i < len(rows):
                s = rows[i]
                if s["status"] == "ok":
                    col, sym = C.GREEN, "✓"
                elif s["status"] == "run":
                    k = 0.55 + (0.45 * abs(math.sin(self._pulse * math.pi))
                                if anim else 0.0)
                    col = C.ACC
                    sym = "◆" if k > 0.6 else "◇"
                else:
                    col, sym = C.TEXT_DIM, "○"
                txt = s["label"]
                if s.get("ms") is not None:
                    txt += f" · {int(s['ms'])} ms"
            else:
                col, sym, txt = C.BORDER_A, "·", ""
            p.setPen(QPen(qcol(col), 2))
            f = QFont("Courier New", 8)
            p.setFont(f)
            p.drawText(QRectF(10, y, 16, rh), Qt.AlignmentFlag.AlignVCenter, sym)
            p.setPen(QPen(qcol(col if i < len(rows) else C.BORDER_A), 1))
            p.drawText(QRectF(30, y, W - 40, rh), Qt.AlignmentFlag.AlignVCenter, txt)
            y += rh
        p.end()


class SystemStatusPanel(QWidget):
    """The stage's SYSTEM STATUS card — the reference's left-hand gauge card.

    Three rings (CPU, MEM, DISK), then a Network section: a two-line
    throughput graph and the numeric rates. Fed by the same metric sampler
    as every other gauge, so nothing is decoration and nothing is sampled
    twice.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(252)
        self.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER_A}; border-radius: 8px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        t = QLabel("SYSTEM STATUS")
        t.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        hdr.addWidget(t)
        hdr.addStretch()
        self._verdict = QLabel("◌ —")
        self._verdict.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._verdict.setStyleSheet(
            f"color: {C.GREEN}; background: transparent; border: none;")
        hdr.addWidget(self._verdict)
        lay.addLayout(hdr)

        rings = QHBoxLayout()
        rings.setSpacing(4)
        self._ring_cpu = _RingGauge("CPU", C.PRI)
        self._ring_mem = _RingGauge("MEM", C.GREEN)
        self._ring_dsk = _RingGauge("DISK", C.ACC)
        for r in (self._ring_cpu, self._ring_mem, self._ring_dsk):
            rings.addWidget(r, 1)
        lay.addLayout(rings)

        lay.addWidget(self._hline())
        netcap = QLabel("Network")
        netcap.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        netcap.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        lay.addWidget(netcap)

        self._net_graph = NetGraph()
        lay.addWidget(self._net_graph)
        self._net = self._row(lay)

        lay.addStretch(1)

    # ── small builders ──────────────────────────────────────────────────────
    @staticmethod
    def _hline() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0; border: none;")
        return sep

    def _row(self, lay: QVBoxLayout) -> QLabel:
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        up_i = QLabel("↑")
        up_i.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        up_i.setFont(QFont("Courier New", 8))
        dn_i = QLabel("↓")
        dn_i.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
        dn_i.setFont(QFont("Courier New", 8))
        v = QLabel("—")
        v.setFont(QFont("Courier New", 8))
        v.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none;")
        v.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(dn_i)
        row.addWidget(up_i)
        row.addStretch()
        row.addWidget(v)
        lay.addLayout(row)
        return v

    # ── data ────────────────────────────────────────────────────────────────
    def refresh(self, snap: dict) -> None:
        cpu = float(snap.get("cpu", 0.0))
        mem = float(snap.get("mem", 0.0))
        disk = float(snap.get("disk", 0.0))
        gpu = float(snap.get("gpu", -1.0))
        tmp = float(snap.get("tmp", -1.0))

        worst = max(cpu, mem, disk, gpu if gpu >= 0 else 0.0)
        if worst >= 85 or (tmp >= 0 and tmp >= 85):
            text, col = "● Load", C.RED
        elif worst >= 65 or (tmp >= 0 and tmp >= 72):
            text, col = "◐ Mid", C.ACC
        else:
            text, col = "● Good", C.GREEN
        self._verdict.setText(text)
        self._verdict.setStyleSheet(
            f"color: {col}; background: transparent; border: none;")

        self._ring_cpu.set_value(cpu, self._fmt_ghz(snap))
        self._ring_mem.set_value(mem, self._fmt_mem(snap))
        self._ring_dsk.set_value(disk, self._fmt_disk(snap))

        up = float(snap.get("net_up", 0.0))
        down = float(snap.get("net_down", 0.0))
        self._net_graph.push(down, up)
        self._net.setText(f"{down:.1f} KB/s  {up:.1f} KB/s")

    @staticmethod
    def _fmt_ghz(snap: dict) -> str:
        try:
            import psutil
            f = psutil.cpu_freq()
            if f and f.current:
                return f"{f.current / 1000:.1f} GHz"
        except Exception:
            pass
        return "—"

    @staticmethod
    def _fmt_mem(snap: dict) -> str:
        try:
            import psutil
            vm = psutil.virtual_memory()
            return f"{vm.used / 2**30:.0f}/{vm.total / 2**30:.0f} GB"
        except Exception:
            return "—"

    @staticmethod
    def _fmt_disk(snap: dict) -> str:
        try:
            import psutil
            du = psutil.disk_usage("C:\\" if _OS == "Windows" else "/")
            return f"{du.free / 2**30:.0f} GB free"
        except Exception:
            return "—"

    def set_services(self, mcp_tools: int | None = None,
                     plugins: int | None = None,
                     brain: str | None = None,
                     wake: str | None = None) -> None:
        """Kept for compatibility: main.py still announces what it loaded.
        The card no longer draws services, but nothing may crash."""
        pass




class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont("Courier New", 13))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            _safe_later(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name, UI colour and voice."""

    saved = pyqtSignal(str, str, str, str)   # assistant_name, user_name, ui_color, voice
    _OW, _OH = 400, 588

    def __init__(self, assistant_name="JARVIS", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Courier New", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Courier New", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── UI colour — colour wheel ─────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — live preview; MainWindow wires it

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont("Courier New", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Courier New", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # If a preview was applied, revert to the colour from launch
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "JARVIS"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Courier New", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Courier New", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Courier New", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JARVIS listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JARVIS can't hear me' is usually 'JARVIS is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Courier New", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Courier New", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what JARVIS hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what JARVIS talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class MemoryOverlay(_HudOverlay):
    """Everything JARVIS has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Courier New", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont("Courier New", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            # Viewport QScrollArea melukis warna default Qt (PUTIH di
            # Windows) kalau tak digaya sendiri — `QScrollArea {…}` tak
            # menjangkaunya. Itu yang bikin teks memori tak bisa dibaca
            # (terverifikasi dari tangkapan layar: 180.228 piksel putih).
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
                f"QScrollBar:vertical {{ background: {C.BG}; width: 8px; "
                f"border: none; }}"
                f"QScrollBar::handle:vertical {{ background: {C.BORDER_B}; "
                f"border-radius: 4px; min-height: 20px; }}"
            )
            scroll.viewport().setStyleSheet("background: transparent;")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Courier New", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont("Courier New", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setStyleSheet("background: transparent;")
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        _safe_later(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        _safe_later(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Courier New", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Courier New", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


# ── CONNECTIONS: one panel for everything that needs setting up ──────────────
#
# This replaces the old row-per-field settings drawer, and the change is
# deliberately about the SHAPE of the screen. A connector is something you
# LOOK at: an icon, a name, one line about what it does, and whether it is
# connected. Only when you open one do you meet its fields — in a dialog wide
# enough to hold a URL or a token without a scrollbar and without hunting.
#
# The panel knows nothing about any specific service: it draws whatever
# connector specs it is handed (plugins that declared PLUGIN_SETTINGS, plus the
# MCP servers from config/mcp.json), so a new plugin or a new MCP server shows
# up here with no UI change at all.

# icon, accent, display name, one-liner. Anything not listed still gets a card
# — generic glyph, cleaned-up title, the plugin's own description — so a
# dropped-in plugin is never invisible just because nobody updated a dictionary.
_CONNECTOR_META: dict[str, tuple[str, str, str, str]] = {
    "telegram_remote":   ("✈️", "#1a5c7a", "Telegram",         "Kendalikan ARIS dari HP"),
    "home_automation":   ("🏠", "#0f6e56", "Home Assistant",   "Lampu, colokan, kunci"),
    "calendar":          ("📅", "#185fa5", "Google Calendar",  "Agenda & pengingat acara"),
    "cloud_agent":       ("☁️", "#534ab7", "Cloud Agent (VPS)", "Tugas berat di server"),
    "meeting_notetaker": ("🎙️", "#993c1d", "Meeting Notetaker", "Rekam rapat → catatan"),
    "rea":               ("🔍", "#854f0b", "REA",              "Analisis binary & aplikasi (MCP)"),
    "composio":          ("🧩", "#534ab7", "Composio",         "1.600+ app: Gmail, Notion, GitHub…"),
}
_CONNECTOR_FALLBACK = ("🔌", "#0d3347", "", "")

# What people actually type in the search box. The names are English because
# that is what the services are called; these are the words a user reaches for
# in Indonesian, and without them "kalender" finds nothing.
_CONNECTOR_KEYWORDS: dict[str, str] = {
    "telegram_remote":   "telegram hp remote jauh chat kendali",
    "home_automation":   "lampu rumah iot tuya smart home kunci colokan",
    "calendar":          "kalender agenda jadwal acara rapat google",
    "cloud_agent":       "vps server cloud tugas berat latar belakang",
    "meeting_notetaker": "rapat rekam notulen transkrip catatan audio",
    "rea":               "rea binary reverse analisis aplikasi debug mcp",
    "composio":          "composio gmail notion github slack kalender drive integrasi app hubspot",
}


def _composio_module():
    """The Composio bridge plugin module, or None. Uses the copy discovery
    already imported; loads it from file otherwise (settings preview, probes),
    so the panel behaves the same in every entry point."""
    mod = sys.modules.get("plugins.composio_connector")
    if mod is not None:
        return mod
    path = Path(__file__).resolve().parent / "plugins" / "composio_connector.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "plugins.composio_connector", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["plugins.composio_connector"] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _connector_keywords(cid: str) -> str:
    return _CONNECTOR_KEYWORDS.get(cid, "")


def _connector_meta(cid: str, fallback_name: str = "", fallback_desc: str = ""):
    icon, accent, name, line = _CONNECTOR_META.get(cid, _CONNECTOR_FALLBACK)
    return icon, accent, (name or fallback_name or cid), (line or fallback_desc)


def _clean_title(title: str) -> str:
    """'📅  CALENDAR' → 'Calendar' — for cards that have no curated name."""
    t = "".join(ch for ch in str(title or "") if ch.isalnum() or ch in " -_()")
    t = " ".join(t.split())
    return t.title() if t else ""


# The form an MCP server gets. Arguments are one-per-line rather than a single
# command string on purpose: there is no quoting rule to get wrong, and a path
# with spaces in it survives the trip.
_MCP_FIELDS = [
    {"key": "command", "label": "Command (program yang dijalankan)", "type": "text",
     "placeholder": "cmd.exe"},
    {"key": "args", "label": "Argumen — satu per baris", "type": "textarea",
     "placeholder": "/d\n/c\nnpx\n-y\nnama-paket@versi\nmcp"},
    {"key": "env", "label": "Environment — KEY=VALUE, satu per baris (opsional)",
     "type": "textarea",
     "placeholder": "REA_ANALYSIS_PROVIDER=ghidra\nGHIDRA_INSTALL_DIR=C:\\ghidra"},
    {"key": "startup_timeout_s", "label": "Batas handshake (detik)", "type": "text",
     "default": "60"},
    {"key": "call_timeout_s", "label": "Batas satu panggilan (detik)", "type": "text",
     "default": "300"},
    {"key": "max_tools", "label": "Maksimum tool yang didaftarkan (0 = semua)",
     "type": "text", "default": "0"},
]


class FuturisticFrame(QFrame):
    """Panel bergaya instrumen: sudut terpotong, gradien, aksen cyan.

    Kenapa digambar, bukan QSS: Qt StyleSheet tidak bisa memotong sudut, dan
    potongan sudut itulah yang membuat panel TORVIS terbaca sebagai
    "instrumen", bukan "kartu web". Satu path + empat garis aksen per panel —
    biayanya kecil, dan tata letak tidak berubah sama sekali (widget tetap
    QFrame biasa, anak-anaknya tetap di tempat yang sama).
    """

    CUT = 10        # piksel yang dipotong dari sudut kiri-atas & kanan-bawah

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        c = min(self.CUT, max(4, min(r.width(), r.height()) // 6))

        path = QPainterPath()
        path.moveTo(r.left() + c, r.top())          # potong kiri-atas
        path.lineTo(r.right(), r.top())
        path.lineTo(r.right(), r.bottom() - c)      # potong kanan-bawah
        path.lineTo(r.right() - c, r.bottom())
        path.lineTo(r.left(), r.bottom())
        path.lineTo(r.left(), r.top() + c)
        path.closeSubpath()

        grad = QLinearGradient(0.0, float(r.top()), 0.0, float(r.bottom()))
        grad.setColorAt(0.0, qcol("#0b1f38"))
        grad.setColorAt(0.55, qcol("#071427"))
        grad.setColorAt(1.0, qcol("#040b16"))
        p.fillPath(path, QBrush(grad))

        edge = QPen(qcol(C.BORDER_B))
        edge.setWidth(1)
        p.setPen(edge)
        p.drawPath(path)

        # Empat aksen pendek di sudut — tanda "aktif" khas panel instrumen.
        tick = QPen(qcol(C.PRI))
        tick.setWidth(2)
        p.setPen(tick)
        p.drawLine(r.left() + c, r.top(), r.left() + c + 16, r.top())
        p.drawLine(r.right() - c - 16, r.bottom(), r.right() - c, r.bottom())
        p.drawLine(r.left(), r.top() + c, r.left(), r.top() + c + 12)
        p.drawLine(r.right(), r.bottom() - c, r.right(), r.bottom() - c - 12)


class ConnectorCard(QFrame):
    """One tile: icon, name, one line, status badge. Click anywhere to open."""

    activated = pyqtSignal(dict)
    _logo_ready = pyqtSignal(object)          # QPixmap, arrived on a worker thread

    def __init__(self, spec: dict, parent=None, width: int = 0):
        super().__init__(parent)
        self._spec = spec
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        if width:
            # A fixed width keeps three columns exactly three columns. Without
            # it the long description decides how wide a card is and the third
            # one is clipped off the right edge of the panel.
            self.setFixedWidth(width)
        self.setStyleSheet(f"""
            ConnectorCard {{
                background: {C.PANEL2};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
            }}
            ConnectorCard:hover {{ border: 1px solid {C.BORDER_B}; background: {C.DARK}; }}
        """)

        row = QHBoxLayout(self)
        row.setContentsMargins(9, 7, 9, 7)
        row.setSpacing(9)

        icon, accent, disp_name, one_liner = _connector_meta(
            spec.get("id", ""), spec.get("name", ""), spec.get("desc", ""))
        self._icon = QLabel(icon)
        self._icon.setFixedSize(34, 34)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setFont(QFont("Segoe UI Emoji", 14))
        self._icon.setStyleSheet(
            f"background: {accent}; color: {C.WHITE}; border-radius: 8px;")
        row.addWidget(self._icon)

        col = QVBoxLayout(); col.setSpacing(1); col.setContentsMargins(0, 0, 0, 0)
        name = QLabel(disp_name)
        name.setFont(_ui_font(10, bold=True))
        name.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        desc = QLabel(one_liner)
        desc.setFont(_ui_font(8))
        desc.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        # Ignored horizontal policy: the text never gets a vote on the card's
        # width. Long lines are elided instead of pushing the layout around.
        for lbl in (name, desc):
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lbl.setMinimumWidth(0)
        name.setToolTip(disp_name)
        desc.setToolTip(one_liner)
        text_w = max(60, (width or 270) - 34 - 92)
        name.setText(QFontMetrics(name.font()).elidedText(
            disp_name, Qt.TextElideMode.ElideRight, text_w))
        desc.setText(QFontMetrics(desc.font()).elidedText(
            one_liner, Qt.TextElideMode.ElideRight, text_w))
        col.addWidget(name); col.addWidget(desc)
        row.addLayout(col, 1)

        self._badge = QLabel()
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setFont(_ui_font(8, bold=True))
        row.addWidget(self._badge)
        self._style_badge(spec.get("status", "setup"), spec.get("status_text", ""))
        # A real app logo beats an emoji — fetched lazily so no card ever
        # waits on the network, and the emoji stays if the fetch fails.
        if str(spec.get("logo") or "").startswith("http"):
            self._logo_ready.connect(self._set_logo)
            threading.Thread(target=self._load_logo,
                             args=(str(spec["logo"]),), daemon=True).start()

    def _load_logo(self, url: str) -> None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
            pm = QPixmap()
            if data and pm.loadFromData(data) and not pm.isNull():
                self._logo_ready.emit(pm)
        except Exception:
            pass                                   # the emoji simply stays

    def _set_logo(self, pm) -> None:
        try:
            self._icon.setPixmap(
                pm.scaled(22, 22, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation))
            self._icon.setStyleSheet("background: transparent;")
        except Exception:
            pass

    def _style_badge(self, status: str, text: str = ""):
        table = {
            "connected":  (text or "✓ tersambung", C.GREEN,   "#06301f"),
            "paused":     (text or "paused",       C.ACC2,    "#2b2200"),
            "error":      (text or "re-connect",   C.RED,     "#2b0a12"),
            "starting":   (text or "menyala…",     C.TEXT_MED, "#00161f"),
            "setup":      (text or "＋",           C.TEXT_MED, None),
        }
        label, color, bg = table.get(status, table["setup"])
        self._badge.setText(label)
        if bg:
            self._badge.setStyleSheet(
                f"color: {color}; background: {bg}; border: 1px solid {color}; "
                f"border-radius: 9px; padding: 2px 8px;")
        else:
            self._badge.setStyleSheet(
                f"color: {color}; background: transparent; "
                f"border: 1px solid {C.BORDER}; border-radius: 9px; padding: 2px 10px;")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self._spec)
        super().mouseReleaseEvent(e)


class ConnectionsOverlay(QWidget):
    """Floating panel — the connector grid.

    Handed a list of connector specs (see JarvisUI._connector_specs) and the
    callbacks that act on them, so the panel itself stays free of any knowledge
    about plugins or MCP.
    """

    _OW = 880
    _OH = 620
    _COLS = 3
    # 880 − margins (20+20) − scrollbar (~6) − two gaps (8+8), split three ways.
    _CARD_W = 272

    def __init__(self, specs: list[dict], callbacks: dict, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConnectionsOverlay {{
                background: rgba(0, 6, 10, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)
        self._specs = list(specs or [])
        self._cb = callbacks or {}
        self._cards: list[ConnectorCard] = []
        self._dialogs: list[QWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        root.addWidget(self._lbl("🔌  CONNECTIONS", 13, True, C.PRI))
        root.addWidget(self._lbl(
            "Semua yang bisa ARIS pakai. Sambungkan sekali, dia memakainya sendiri "
            "saat mengobrol.", 9, color=C.TEXT_DIM))

        search_row = QHBoxLayout(); search_row.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Cari konektor…  (telegram, kalender, rea)")
        self._search.setFixedHeight(30)
        self._search.setFont(_ui_font(9))
        self._search.setStyleSheet(
            f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 4px; padding: 3px 10px; }}"
            f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")
        self._search.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search, 1)
        root.addLayout(search_row)

        root.addWidget(self._lbl("SAMBUNGKAN LANGSUNG", 8, color=C.TEXT_DIM))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        inner = QWidget(); inner.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(inner)
        self._grid.setContentsMargins(0, 0, 6, 0)
        self._grid.setSpacing(8)
        # Cards keep their size; the leftover vertical space goes to a stretch
        # row at the bottom instead of inflating every row (which is what put a
        # hand's width of air between two rows of tiles).
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        self._empty = self._lbl("Tidak ada konektor yang cocok dengan pencarian itu.",
                                9, color=C.TEXT_DIM)
        self._empty.hide()
        root.addWidget(self._empty)

        # While a source (Composio) refreshes in the background, the panel
        # polls it — no spinner, no modal, the cards simply arrive.
        self._poll = QTimer(self)
        self._poll.setInterval(700)
        self._poll.timeout.connect(self._poll_tick)
        try:
            st = self._cb.get("composio_state")
            self._gen = (st() or {}).get("gen", 0) if callable(st) else 0
        except Exception:
            self._gen = 0

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        refresh_btn = QPushButton("↻  SEGARKAN")
        refresh_btn.setFixedHeight(32)
        refresh_btn.setFont(_ui_font(9))
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 0 14px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(refresh_btn)

        add_btn = QPushButton("＋  TAMBAH TOOL SENDIRI  (server MCP)")
        add_btn.setFixedHeight(32)
        add_btn.setFont(_ui_font(9, bold=True))
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px dashed {C.PRI_DIM}; border-radius: 4px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        add_btn.clicked.connect(lambda: self._open_dialog({"kind": "mcp", "is_new": True}))
        btn_row.addWidget(add_btn, 1)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(32)
        close_btn.setFont(_ui_font(9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 0 14px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._fill_grid(self._specs)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI):
        w = QLabel(txt); w.setWordWrap(True)
        w.setFont(_ui_font(fs, bold))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _fill_grid(self, specs: list[dict]):
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards = []
        row, col, last_group, row_dirty = 0, 0, None, False
        for spec in specs:
            group = spec.get("group")
            if group != last_group:
                last_group = group
                if group:
                    # A section header spanning the full grid width — the
                    # Composio catalog is a crowd of its own, and one label
                    # says so without touching any card. A new group never
                    # shares a row with cards: without the bump the header (and
                    # the first catalog card) landed in the same grid cell as
                    # whatever card sat mid-row — the Composio header used to
                    # print on top of the REA card.
                    if row_dirty:
                        row += 1
                        row_dirty = False
                    self._grid.addWidget(
                        self._lbl(group.upper(), 8, color=C.TEXT_DIM),
                        row, 0, 1, self._COLS)
                    row += 1
                col = 0
            card = ConnectorCard(spec, parent=self, width=self._CARD_W)
            card.activated.connect(self._open_dialog)
            self._grid.addWidget(card, row, col)
            col += 1
            row_dirty = True
            if col >= self._COLS:
                col = 0
                row += 1
                row_dirty = False
            self._cards.append(card)
        rows = (len(specs) + self._COLS) // self._COLS + 1
        for r in range(row, row + 2):            # absorb the slack at the bottom
            self._grid.setRowStretch(r, 1)
        self._empty.setVisible(not specs)
        if any(s.get("busy") for s in specs):
            self._start_polling()

    def _apply_filter(self, text: str):
        needle = (text or "").strip().lower()
        if not needle:
            self._fill_grid(self._specs)
            return
        # Search what the user can see AND the words they would think of —
        # "kalender" has to find "Google Calendar".
        def hay(spec: dict) -> str:
            return " ".join(str(spec.get(k, "")) for k in
                            ("id", "name", "desc", "keywords")).lower()
        hits = [s for s in self._specs if needle in hay(s)]
        self._fill_grid(hits)

    def _open_dialog(self, spec: dict):
        cw = self.parentWidget() or self
        dlg = ConnectorDialog(spec, self._cb, parent=cw)
        dlg.adjustSize()
        w = ConnectorDialog._OW
        h = min(dlg.sizeHint().height() + 20, cw.height() - 40)
        dlg.setGeometry((cw.width() - w) // 2, max(12, (cw.height() - h) // 2), w, h)
        dlg.changed.connect(self.reload)
        dlg.show(); dlg.raise_()
        self._dialogs.append(dlg)

    def reload(self):
        """Re-ask the app for specs (a save may have changed a status). The
        search text goes along: a composio search must run against the whole
        catalog, not against whatever the last unfiltered fetch returned."""
        loader = self._cb.get("reload")
        if callable(loader):
            try:
                self._specs = loader(self._search.text()) or []
            except TypeError:                   # loader takes no argument
                self._specs = loader() or []
        self._apply_filter(self._search.text())

    def _refresh(self):
        cb = self._cb.get("composio_refresh")
        if callable(cb):
            try:
                cb()
            except Exception:
                pass
        self.reload()

    def _start_polling(self):
        try:
            st = self._cb.get("composio_state")
            self._gen = (st() or {}).get("gen", -1) if callable(st) else -1
        except Exception:
            self._gen = -1
        self._poll.start(700)

    def _poll_tick(self):
        st = self._cb.get("composio_state")
        data = st() if callable(st) else {}
        if not data.get("busy"):
            self._poll.stop()
            if data.get("gen") != self._gen:
                self.reload()


class ConnectorDialog(QWidget):
    """The form behind a card — one connector, wide enough for a URL or a token.

    Deliberately a dialog and not another row in a drawer: a token field that
    needs horizontal scrolling to be read is how the wrong key gets pasted.
    """

    _OW = 560
    changed = pyqtSignal()
    _test_done = pyqtSignal(str, bool, str)      # id, ok, message
    _save_done = pyqtSignal(str, str)            # id, message ('' = ok)
    _link_done = pyqtSignal(bool, str)           # ok, redirect_url or error
    _poll_done = pyqtSignal(str, str)            # status, connected-account id
    _disc_done = pyqtSignal(str)                 # error message ('' = ok)

    def __init__(self, spec: dict, callbacks: dict, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConnectorDialog {{
                background: rgba(0, 8, 13, 250);
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)
        self._spec = spec
        self._cb = callbacks or {}
        self._kind = spec.get("kind", "plugin")
        self._cid = spec.get("id", "")
        self._is_new = bool(spec.get("is_new"))
        self._widgets: dict[str, object] = {}
        self._types: dict[str, str] = {}
        self._test_done.connect(self._on_test_done)
        self._save_done.connect(self._on_save_done)
        self._link_done.connect(self._on_link_done)
        self._poll_done.connect(self._on_poll_done)
        self._disc_done.connect(self._on_disc_done)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._on_poll_tick)
        self._poll_n = 0

        icon, accent, disp_name, one_liner = _connector_meta(
            self._cid, spec.get("name", ""), spec.get("desc", ""))
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(9)

        head = QHBoxLayout(); head.setSpacing(10)
        ic = QLabel(icon)
        ic.setFixedSize(42, 42)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setFont(QFont("Segoe UI Emoji", 17))
        ic.setStyleSheet(f"background: {accent}; border-radius: 9px;")
        head.addWidget(ic)
        titles = QVBoxLayout(); titles.setSpacing(1)
        t = QLabel(disp_name or self._cid or "Konektor baru")
        t.setFont(_ui_font(11, bold=True))
        t.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        d = QLabel(one_liner or spec.get("desc", ""))
        d.setFont(_ui_font(8))
        d.setWordWrap(True)
        d.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        titles.addWidget(t); titles.addWidget(d)
        head.addLayout(titles, 1)
        root.addLayout(head)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER};")
        root.addWidget(line)

        form = QVBoxLayout(); form.setSpacing(7)
        if self._kind == "composio_app":
            # An app catalog card has no fields to type — it is a status you
            # act on, so the form shows what Composio says, not an empty box.
            self._build_composio_body(form)
        else:
            if self._kind == "mcp" and self._is_new:
                self._add_field(form, {"key": "id", "label": "Nama server (id)",
                                       "type": "text", "placeholder": "mis. my_tools"})
            self._add_field(form, {"key": "enabled", "label": "Aktif", "type": "toggle"})
            # A dialog opened straight from "add your own tool" carries no field
            # list; an MCP server always gets the same form, whoever opened it.
            fields = spec.get("fields") or (_MCP_FIELDS if self._kind == "mcp" else [])
            for field in fields:
                self._add_field(form, field)
        root.addLayout(form)

        self._status = self._lbl("")
        root.addWidget(self._status)

        btns = QHBoxLayout(); btns.setSpacing(8)
        if self._kind == "composio_app":
            self._composio_buttons(btns)
        else:
            save = QPushButton("▸  SIMPAN")
            save.setFixedHeight(32); save.setFont(_ui_font(9, bold=True))
            save.setCursor(Qt.CursorShape.PointingHandCursor)
            save.setStyleSheet(f"""
                QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                    border: 1px solid {C.PRI}; border-radius: 4px; padding: 0 14px; }}
                QPushButton:hover {{ background: {C.DARK}; }}
            """)
            save.clicked.connect(self._save)
            btns.addWidget(save)

            if self._kind == "mcp" and not self._is_new:
                test = QPushButton("⚡  TES KONEKSI")
                test.setFixedHeight(32); test.setFont(_ui_font(9))
                test.setCursor(Qt.CursorShape.PointingHandCursor)
                test.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.ACC2};
                        border: 1px solid {C.ACC2}; border-radius: 4px; padding: 0 12px; }}
                    QPushButton:hover {{ background: #2b2200; }}
                """)
                test.clicked.connect(self._test)
                btns.addWidget(test)

                delete = QPushButton("HAPUS")
                delete.setFixedHeight(32); delete.setFont(_ui_font(9))
                delete.setCursor(Qt.CursorShape.PointingHandCursor)
                delete.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.RED};
                        border: 1px solid {C.RED}; border-radius: 4px; padding: 0 12px; }}
                    QPushButton:hover {{ background: #2b0a12; }}
                """)
                delete.clicked.connect(self._delete)
                btns.addWidget(delete)
            elif callable(spec.get("action")):
                # a plugin that declared its own test/connect action (Telegram does)
                test = QPushButton(f"⚡  {spec.get('action_label') or 'TES'}")
                test.setFixedHeight(32); test.setFont(_ui_font(9))
                test.setCursor(Qt.CursorShape.PointingHandCursor)
                test.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.ACC2};
                        border: 1px solid {C.ACC2}; border-radius: 4px; padding: 0 12px; }}
                    QPushButton:hover {{ background: #2b2200; }}
                """)
                test.clicked.connect(self._test_plugin_action)
                btns.addWidget(test)

        btns.addStretch(1)
        close = QPushButton("TUTUP")
        close.setFixedHeight(32); close.setFont(_ui_font(9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 0 14px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        btns.addWidget(close)
        root.addLayout(btns)

    # ── building ─────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.TEXT_DIM):
        w = QLabel(txt); w.setWordWrap(True)
        w.setFont(_ui_font(fs, bold))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _add_field(self, form: QVBoxLayout, field: dict):
        key = field.get("key")
        if not key:
            return
        ftype = (field.get("type") or "text").lower()
        label = field.get("label") or key
        stored = self._spec.get("values", {}).get(key, field.get("default"))

        if ftype != "label":
            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

        if ftype == "choice":
            w = QComboBox()
            w.addItems([str(o) for o in field.get("options", [])])
            w.setFont(_ui_font(9)); w.setFixedHeight(30)
            w.setStyleSheet(
                f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
                f"border: 1px solid {C.BORDER}; border-radius: 4px; padding: 2px 8px; }}"
                f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
                f"selection-background-color: {C.PRI_GHO}; }}")
            if stored is not None:
                w.setCurrentText(str(stored))
        elif ftype == "toggle":
            w = QPushButton(); w.setCheckable(True); w.setChecked(bool(stored))
            w.setFixedHeight(28); w.setFont(_ui_font(8, True))
            w.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(w)
            w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
        elif ftype == "textarea":
            w = QPlainTextEdit()
            w.setPlainText("\n".join(str(x) for x in (stored or [])))
            w.setFont(_ui_font(9)); w.setFixedHeight(74)
            w.setStyleSheet(
                f"QPlainTextEdit {{ background: #000d12; color: {C.TEXT}; "
                f"border: 1px solid {C.BORDER}; border-radius: 4px; padding: 4px 8px; }}"
                f"QPlainTextEdit:focus {{ border: 1px solid {C.PRI}; }}")
            if field.get("placeholder"):
                w.setPlaceholderText(str(field["placeholder"]))
        elif ftype == "label":
            w = self._lbl(label, 8, color=C.TEXT_MED)
        else:                                    # text / password
            w = QLineEdit("" if stored is None else str(stored))
            w.setFont(_ui_font(10)); w.setFixedHeight(30)
            w.setStyleSheet(
                f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                f"border: 1px solid {C.BORDER}; border-radius: 4px; padding: 4px 9px; }}"
                f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")
            if field.get("placeholder"):
                w.setPlaceholderText(str(field["placeholder"]))
            if ftype == "password":
                w.setEchoMode(QLineEdit.EchoMode.Password)

        self._widgets[key] = w
        self._types[key] = ftype
        form.addWidget(w)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 4px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 4px; }}")

    # ── data ─────────────────────────────────────────────────────────────────
    def _gather(self) -> dict:
        out = {}
        for key, w in self._widgets.items():
            t = self._types.get(key, "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            elif t == "textarea":
                out[key] = [ln.strip() for ln in w.toPlainText().splitlines() if ln.strip()]
            elif t == "label":
                continue
            else:
                out[key] = w.text().strip()
        return out

    def _save(self):
        values = self._gather()
        if self._kind == "mcp":
            sid = values.pop("id", None) or self._cid
            # KEY=VALUE lines → the dict the config file stores
            env = {}
            for line in values.get("env", []) or []:
                if "=" not in line:
                    self._show(f"Baris environment harus KEY=VALUE: '{line}'", ok=False)
                    return
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
            cfg = {
                "command": values.get("command", ""),
                "args": values.get("args", []),
                "enabled": values.get("enabled", True),
                "startup_timeout_s": values.get("startup_timeout_s", 60),
                "call_timeout_s": values.get("call_timeout_s", 300),
                "max_tools": values.get("max_tools", 0),
            }
            if env:
                cfg["env"] = env
            saver = self._cb.get("save_mcp")
            err = saver(sid, cfg) if callable(saver) else "no saver"
            if err:
                self._show(err, ok=False)
                return
            self._cid = sid
            self._is_new = False
            self._show("Tersimpan. Tekan TES KONEKSI untuk memastikannya menyala.", ok=True)
            self.changed.emit()
        else:
            saver = self._cb.get("save_plugin")
            if not callable(saver):
                self._show("Penyimpan tidak tersedia.", ok=False)
                return
            err = saver(self._cid, values) or ""
            if err:
                self._show(err, ok=False)
                return
            self._show("Tersimpan ✓", ok=True)
            self.changed.emit()

    def _test(self):
        tester = self._cb.get("test_mcp")
        if not callable(tester):
            return
        self._show("Menyambungkan… (bisa sampai satu menit)", ok=None)
        cid = self._cid

        def worker():
            try:
                msg = tester(cid)
            except Exception as e:
                msg = str(e)
            ok = isinstance(msg, str) and msg.startswith("Connected")
            self._test_done.emit(cid, ok, str(msg))

        threading.Thread(target=worker, daemon=True).start()

    def _delete(self):
        deleter = self._cb.get("delete_mcp")
        if not callable(deleter):
            return
        err = deleter(self._cid) or ""
        if err:
            self._show(err, ok=False)
            return
        self._show("Server dihapus.", ok=True)
        self.changed.emit()

    def _test_plugin_action(self):
        """Run a plugin's own test/connect action on what is in the form."""
        action = self._spec.get("action")
        if not callable(action):
            return
        values = self._gather()
        values.pop("enabled", None)
        self._show("Menguji…", ok=None)
        cid = self._cid

        def worker():
            try:
                res = action(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(cid, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _show(self, msg: str, ok=True):
        color = C.TEXT_DIM if ok is None else (C.GREEN if ok else C.RED)
        self._status.setText(msg)
        self._status.setStyleSheet(f"color: {color}; background: transparent;")

    def _on_test_done(self, cid: str, ok: bool, msg: str):
        self._show(msg, ok=ok)
        self.changed.emit()

    def _on_save_done(self, cid: str, msg: str):
        self._show(msg or "Tersimpan ✓", ok=not msg)

    # ── a Composio app card: status, connect, disconnect ──────────────────────

    def _build_composio_body(self, form: QVBoxLayout):
        spec = self._spec
        connected = spec.get("status") == "connected"
        managed = spec.get("managed", True)
        scheme = str(spec.get("auth_scheme") or "OAuth").lower()
        if connected:
            form.addWidget(self._lbl(
                "✓ Tersambung. Token disimpan di sisi Composio dan diperbarui "
                "otomatis — Jarvis tidak pernah memegangnya.", 9, color=C.TEXT))
        elif not managed:
            form.addWidget(self._lbl(
                "App ini tidak bisa disambungkan otomatis — penyedia layanan "
                "mewajibkan kredensial sendiri (API key), dan Composio tidak "
                "mengelola login-nya. Koneksinya disiapkan di dashboard "
                "composio.dev bila memang dibutuhkan.", 9, color=C.TEXT_MED))
        else:
            form.addWidget(self._lbl(
                "Belum tersambung. Tekan SAMBUNGKAN — browser terbuka, login "
                "sekali di layanan tujuan, selesai.", 9, color=C.TEXT_MED))
        if spec.get("conn_id"):
            form.addWidget(self._lbl(
                f"ID koneksi: {spec['conn_id']}  ·  metode: {scheme}", 8))
        form.addWidget(self._lbl(
            "Koneksi terikat pada User ID Composio (kartu Composio). Semua app "
            "yang disambungkan di sini otomatis muncul di panel Connections.",
            8))

    def _composio_buttons(self, btns: QHBoxLayout):
        slug = self._spec.get("slug") or ""
        if not slug:                       # the "…dan N app lainnya" tile
            return
        connected = self._spec.get("status") == "connected"
        managed = spec_managed = self._spec.get("managed", True)
        # Apps without Composio-managed auth get no connect button: clicking it
        # would only end in "Tidak bisa menyiapkan auth config". The card body
        # explains what those apps need instead.
        if connected or managed:
            connect_btn = QPushButton("🔗  SAMBUNGKAN" if not connected
                                      else "⇄  SAMBUNGKAN ULANG")
            connect_btn.setFixedHeight(32); connect_btn.setFont(_ui_font(9, bold=True))
            connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            connect_btn.setStyleSheet(f"""
                QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                    border: 1px solid {C.PRI}; border-radius: 4px; padding: 0 14px; }}
                QPushButton:hover {{ background: {C.DARK}; }}
            """)
            connect_btn.clicked.connect(self._composio_connect)
            btns.addWidget(connect_btn)

        if connected:
            disc_btn = QPushButton("PUTUSKAN")
            disc_btn.setFixedHeight(32); disc_btn.setFont(_ui_font(9))
            disc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            disc_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.RED};
                    border: 1px solid {C.RED}; border-radius: 4px; padding: 0 12px; }}
                QPushButton:hover {{ background: #2b0a12; }}
            """)
            disc_btn.clicked.connect(self._composio_disconnect)
            btns.addWidget(disc_btn)

        refresh_btn = QPushButton("↻  SEGARKAN")
        refresh_btn.setFixedHeight(32); refresh_btn.setFont(_ui_font(9))
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 0 12px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        refresh_btn.clicked.connect(self._composio_refresh)
        btns.addWidget(refresh_btn)

    def _composio_connect(self):
        slug = self._spec.get("slug") or ""
        cb = self._cb.get("composio_connect")
        if not callable(cb):
            self._show("Jembatan Composio tidak tersedia.", ok=False)
            return
        self._show("Menyiapkan tautan otorisasi…", ok=None)

        def worker():
            try:
                ok, msg = cb(slug)
            except Exception as e:                       # noqa: BLE001
                ok, msg = False, str(e)
            self._link_done.emit(bool(ok), str(msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_link_done(self, ok: bool, msg: str):
        if not ok:
            self._show(msg, ok=False)
            return
        try:
            webbrowser.open(msg)
        except Exception as e:                           # noqa: BLE001
            self._show(f"Buka tautan ini manual: {msg}  ({e})", ok=False)
            return
        self._show("Browser terbuka — silakan login di layanan tujuan. "
                   "Menunggu persetujuan…", ok=None)
        self._poll_n = 0
        self._poll_timer.start(2500)

    def _on_poll_tick(self):
        self._poll_n += 1
        if self._poll_n > 48:              # ~2 minutes is patience enough
            self._poll_timer.stop()
            self._show("Waktu tunggu habis. Kalau login sudah selesai, "
                       "tekan SEGARKAN.", ok=False)
            return
        cb = self._cb.get("composio_poll")
        if not callable(cb):
            self._poll_timer.stop()
            return
        slug = self._spec.get("slug") or ""

        def worker():
            try:
                status, conn_id = cb(slug)
            except Exception as e:                       # noqa: BLE001
                status, conn_id = "ERROR", str(e)
            self._poll_done.emit(str(status), str(conn_id))

        threading.Thread(target=worker, daemon=True).start()

    def _on_poll_done(self, status: str, conn_id: str):
        status = (status or "").upper()
        if status == "ACTIVE":
            self._poll_timer.stop()
            self._spec["status"] = "connected"
            self._spec["conn_id"] = conn_id
            inv = self._cb.get("composio_refresh")
            if callable(inv):
                try:
                    inv()
                except Exception:
                    pass
            self._show("Tersambung ✓ — kartu di panel kini bertanda aktif.", ok=True)
            self.changed.emit()
        elif status in ("EXPIRED", "FAILED", "INVALID", "REJECTED"):
            self._poll_timer.stop()
            self._show(f"Composio melaporkan: {status}. Coba sambungkan ulang.",
                       ok=False)
        # INITIALIZING / INITIATED → keep waiting quietly

    def _composio_disconnect(self):
        conn_id = self._spec.get("conn_id") or ""
        cb = self._cb.get("composio_disconnect")
        if not callable(cb):
            self._show("Jembatan Composio tidak tersedia.", ok=False)
            return
        self._show("Memutuskan koneksi…", ok=None)

        def worker():
            try:
                err = cb(conn_id) or ""
            except Exception as e:                       # noqa: BLE001
                err = str(e)
            self._disc_done.emit(err)

        threading.Thread(target=worker, daemon=True).start()

    def _on_disc_done(self, err: str):
        if err:
            self._show(err, ok=False)
            return
        self._spec["status"] = "setup"
        self._spec["conn_id"] = ""
        inv = self._cb.get("composio_refresh")
        if callable(inv):
            try:
                inv()
            except Exception:
                pass
        self._show("Koneksi diputus.", ok=True)
        self.changed.emit()

    def _composio_refresh(self):
        inv = self._cb.get("composio_refresh")
        if callable(inv):
            try:
                inv()
            except Exception:
                pass
        self._show("Menyegarkan katalog…", ok=None)
        self.changed.emit()


class ProvidersOverlay(QWidget):
    """The model picker — every custom AI provider on one board.

    WHAT IT IS FOR
        Coding agents let you keep several API keys and pick which model
        does the work. This is that board: one card per provider, the model
        name read from the config (not retyped), and a single ACTIVE choice
        that decides which one leads the heavy-task ladder in
        core/gemini.py. The rest stay behind it as fallbacks, so switching
        never removes anything.

    ACTIONS
        AKTIFKAN  — make this provider the one heavy tasks go to
        EDIT      — open the existing form for this exact provider
        ×         — delete (two clicks: the label turns into YAKIN?)
        + TAMBAH  — open the form with a blank id to add another API
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        from memory.config_manager import (get_active_provider,
                                           list_provider_ids,
                                           get_provider_config)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ProvidersOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(520)
        self.setMinimumHeight(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(8)

        hdr = QLabel("🧠  AI PROVIDERS — otak custom (percakapan & tugas berat)")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        root.addWidget(hdr)

        ids = list_provider_ids()
        active = get_active_provider() or (ids[0] if ids else "")
        self._active = active
        note = QLabel(
            ("Aktif: " + active) if active else
            "Belum ada provider — tambahkan satu di bawah.")
        note.setFont(QFont("Courier New", 8))
        note.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        root.addWidget(note)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        self._cards = QVBoxLayout(host)
        self._cards.setSpacing(6)
        self._cards.setContentsMargins(0, 0, 6, 0)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        self._status = QLabel("")
        self._status.setFont(QFont("Courier New", 8))
        self._status.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        root.addWidget(self._status)

        row = QHBoxLayout(); row.setSpacing(8)
        add = QPushButton("＋  TAMBAH MODEL")
        add.setFixedHeight(32)
        add.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}""")
        add.clicked.connect(lambda: self._edit(""))
        row.addWidget(add)

        close = QPushButton("TUTUP")
        close.setFixedHeight(32)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}""")
        close.clicked.connect(self.hide)
        row.addWidget(close)
        root.addLayout(row)

        self._editor = None
        self._refresh()

    # ── cards ───────────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        from memory.config_manager import (get_active_provider,
                                           list_provider_ids,
                                           get_provider_config)
        while self._cards.count():
            it = self._cards.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        ids = list_provider_ids()
        self._active = get_active_provider() or (ids[0] if ids else "")
        if not ids:
            empty = QLabel("Belum ada model custom.\nTekan ＋ TAMBAH MODEL "
                           "untuk memasukkan API pertama.")
            empty.setFont(QFont("Courier New", 9))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._cards.addWidget(empty)
            return

        for pid in ids:
            self._cards.addWidget(self._card(pid, get_provider_config(pid)))
        self._cards.addStretch(1)

    def _card(self, pid: str, cfg: dict) -> QWidget:
        is_active = pid == self._active
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{ background: {'#03181f' if is_active else 'transparent'};
                border: 1px {C.PRI_DIM if is_active else C.BORDER};
                border-radius: 5px; }}""")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(4)

        top = QHBoxLayout(); top.setSpacing(8)
        model = QLabel(str(cfg.get("model") or "(model belum diisi)"))
        model.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        model.setStyleSheet(f"color: {C.PRI if is_active else C.TEXT}; "
                            f"background: transparent; border: none;")
        top.addWidget(model)
        if is_active:
            badge = QLabel("OTAK UTAMA")
            badge.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            badge.setStyleSheet("color: #0b2b1f; background: #5DCAA5; "
                                "border-radius: 3px; padding: 1px 5px;")
            top.addWidget(badge)
        top.addStretch()
        box.addLayout(top)

        meta = QLabel(f"{pid}  ·  {str(cfg.get('base_url') or '')}")
        meta.setFont(QFont("Courier New", 7))
        meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        box.addWidget(meta)

        tiers = cfg.get("tiers") or []
        if tiers:
            t = QLabel("tier: " + ", ".join(str(x) for x in tiers))
            t.setFont(QFont("Courier New", 7))
            t.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            box.addWidget(t)

        # Every model stored on this key, one click away. Choosing one is a
        # config write — the ladder reads `model` on the next call, so the
        # switch lands without a restart.
        models = [str(m) for m in (cfg.get("models") or [])]
        if len(models) > 1:
            mrow = QHBoxLayout(); mrow.setSpacing(4)
            for m in models:
                on = (m == cfg.get("model"))
                mb = QPushButton(m)
                mb.setFixedHeight(22)
                mb.setFont(QFont("Courier New", 7, QFont.Weight.Bold if on else QFont.Weight.Normal))
                mb.setCursor(Qt.CursorShape.PointingHandCursor)
                mb.setStyleSheet(
                    f"QPushButton {{ background: {C.PRI_GHO if on else 'transparent'};"
                    f" color: {C.PRI if on else C.TEXT_MED};"
                    f" border: 1px solid {C.PRI_DIM if on else C.BORDER};"
                    f" border-radius: 9px; padding: 0 8px; }}"
                    f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}")
                mb.clicked.connect(lambda _=False, mm=m, p=pid: self._use_model(p, mm))
                mrow.addWidget(mb)
            mrow.addStretch()
            box.addLayout(mrow)

        btns = QHBoxLayout(); btns.setSpacing(6)
        act = QPushButton("AKTIFKAN" if not is_active else "SEDANG AKTIF")
        act.setFixedHeight(26)
        act.setEnabled(not is_active)
        act.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        act.setCursor(Qt.CursorShape.PointingHandCursor)
        act.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 0 8px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}""")
        act.clicked.connect(lambda _=False, p=pid: self._activate(p))
        btns.addWidget(act)

        edit = QPushButton("EDIT")
        edit.setFixedHeight(26)
        edit.setFont(QFont("Courier New", 8))
        edit.setCursor(Qt.CursorShape.PointingHandCursor)
        edit.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}""")
        edit.clicked.connect(lambda _=False, p=pid: self._edit(p))
        btns.addWidget(edit)

        drop = QPushButton("×")
        drop.setFixedSize(30, 26)
        drop.setFont(QFont("Courier New", 10))
        drop.setCursor(Qt.CursorShape.PointingHandCursor)
        drop.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: #ff6688; border-color: #ff6688; }}""")
        drop.clicked.connect(lambda _=False, p=pid, b=drop: self._delete(p, b))
        btns.addWidget(drop)
        btns.addStretch()
        box.addLayout(btns)
        return card

    # ── actions ─────────────────────────────────────────────────────────────
    def _use_model(self, pid: str, model: str) -> None:
        """Switch which stored model this provider uses."""
        from memory.config_manager import save_provider_config
        save_provider_config(pid, {"model": model})
        self._status.setText(f"{pid} → {model}. Berlaku pada tugas berikutnya.")
        self._refresh()
        self.changed.emit()

    def _activate(self, pid: str) -> None:
        from memory.config_manager import save_active_provider
        save_active_provider(pid)
        self._status.setText(f"Otak tugas berat → {pid}. Berlaku pada tugas "
                             f"berikutnya, tanpa restart.")
        self._refresh()
        self.changed.emit()

    def _delete(self, pid: str, btn: QPushButton) -> None:
        if btn.text() != "×":
            from memory.config_manager import delete_provider
            delete_provider(pid)
            self._status.setText(f"Provider '{pid}' dihapus.")
            self._refresh()
            self.changed.emit()
            return
        btn.setText("YAKIN?")
        btn.setFixedWidth(64)
        _safe_later(4000, lambda: (btn.setText("×"),
                                    btn.setFixedWidth(30)))

    def _edit(self, pid: str) -> None:
        """Open the form for one provider, CENTRED over the board.

        A child widget with no geometry shows at (0,0) at its default size —
        which is the top-left corner, clipped by the HUD. Every other overlay
        in this app is placed by MainWindow._centre_overlay; this one is
        opened from inside a panel, so it does the same three things itself:
        size it, centre it, raise it above the board.
        """
        host = self.parentWidget()
        ed = ProviderSettingsOverlay(pid=pid, parent=host)
        ed.saved.connect(self._on_saved)
        ed.adjustSize()
        if host is not None:
            ed.setGeometry(
                max(0, (host.width()  - ed.width())  // 2),
                max(0, (host.height() - ed.height()) // 2),
                ed.width(), ed.height(),
            )
        ed.show()
        ed.raise_()
        ed.activateWindow()
        self._editor = ed

    def _on_saved(self, pid: str) -> None:
        self._status.setText(f"'{pid}' tersimpan.")
        # Back to the board with the change visible: save → see the updated
        # card, rather than a form that stays open over it.
        if getattr(self, "_editor", None) is not None:
            self._editor.hide()
        self._refresh()
        self.changed.emit()


class _ModelChips(QWidget):
    """Model names as chips: type one, press Enter, it sticks.

    One API key usually exposes several models (deepseek-v4, glm-5.3,
    gemini-3.8 …). Entering them as a list — the way a coding agent's model
    picker does — beats retyping the key's single model every time you want
    to try another. The first chip is the one the ladder uses until another
    is chosen on the provider board.
    """

    changed = pyqtSignal()

    def __init__(self, values=None, parent=None):
        super().__init__(parent)
        self._names: list[str] = []
        self.setStyleSheet("background: transparent;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        self._chips = QHBoxLayout()
        self._chips.setSpacing(5)
        self._chips.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._chips)

        self._input = QLineEdit()
        self._input.setFont(QFont("Courier New", 10))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(
            f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")
        self._input.setPlaceholderText(
            "ketik nama model, tekan ENTER untuk menambah — bisa lebih dari satu")
        self._input.returnPressed.connect(self._on_enter)
        lay.addWidget(self._input)

        for v in (values or []):
            self._add(v, emit=False)

    # ── public ──────────────────────────────────────────────────────────────
    def chips(self) -> list[str]:
        return list(self._names)

    def add(self, name: str) -> None:
        self._add(name, emit=True)

    # ── internals ───────────────────────────────────────────────────────────
    def _on_enter(self) -> None:
        self._add(self._input.text(), emit=True)
        self._input.clear()

    def _add(self, name: str, emit: bool) -> None:
        name = (name or "").strip()
        if not name or name in self._names:
            return
        self._names.append(name)

        chip = QFrame()
        chip.setStyleSheet(f"QFrame {{ background: {C.PRI_GHO}; "
                           f"border: 1px solid {C.PRI_DIM}; border-radius: 10px; }}")
        row = QHBoxLayout(chip)
        row.setContentsMargins(8, 2, 4, 2)
        row.setSpacing(4)
        lbl = QLabel(name)
        lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
        row.addWidget(lbl)
        x = QPushButton("×")
        x.setFixedSize(16, 16)
        x.setFont(QFont("Courier New", 8))
        x.setCursor(Qt.CursorShape.PointingHandCursor)
        x.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                        f"border: none; }} QPushButton:hover {{ color: #ff6688; }}")
        x.clicked.connect(lambda _=False, n=name, c=chip: self._remove(n, c))
        row.addWidget(x)

        self._chips.addWidget(chip)
        if emit:
            self.changed.emit()

    def _remove(self, name: str, chip: QWidget) -> None:
        if name in self._names:
            self._names.remove(name)
        chip.setParent(None)
        chip.deleteLater()
        self.changed.emit()


class ProviderSettingsOverlay(QWidget):
    """Floating overlay — configure an external AI provider.

    WHAT IT CONFIGURES
        One OpenAI-compatible provider (an aggregator like FreeLLM, or a
        local runtime) as a rung on the model ladder in core/gemini.py —
        the same system the cloud brain expects, driven from the interface
        instead of a JSON file: base_url, api_key, model, how many seconds
        the ladder waits for it (an aggregator re-routing between upstream
        models is working, not stuck), whether the model can read images,
        which tiers it serves, and whether it leads the ladder or only
        backs the Gemini names up.

    WHERE IT SAVES
        config/api_keys.json under "providers" — see memory/config_manager.
        The API key is stored exactly like the Gemini key: plaintext, on
        this machine, to be treated like a password file. Values take
        effect on the next one-shot call, so every coding skill on the
        SMART tier reroutes through here without a restart.
    """

    saved = pyqtSignal(str)              # provider id, after a successful save
    _test_done = pyqtSignal(bool, str)   # ok, message
    _OW = 460

    def __init__(self, pid=None, parent=None):
        super().__init__(parent)
        from memory.config_manager import (
            get_provider_config, get_provider_first, list_provider_ids,
        )

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ProviderSettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        # Which provider this form edits.
        #   pid=None  — opened the old way: the first provider (or a fresh id)
        #   pid=""    — ADD: a blank form for a new gateway
        #   pid="x"   — EDIT exactly that provider
        ids = list_provider_ids()
        if pid is None:
            pid = ids[0] if ids else "freellm"
        pid = (pid or "").strip()
        cfg = get_provider_config(pid) if pid else {}

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("🔌  AI PROVIDER", 12, True))
        root.addWidget(self._lbl(
            "An external model as a rung on the ladder — heavy tasks (coding "
            "and the like) go here first, Gemini stays as the fallback.",
            8, color=C.TEXT_DIM))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        form = QVBoxLayout(inner)
        form.setContentsMargins(0, 0, 6, 0)
        form.setSpacing(6)

        form.addWidget(self._lbl(
            "PROVIDER ID  (change it only to create a second provider)",
            8, color=C.TEXT_DIM))
        self._pid = QLineEdit(pid)
        self._pid.setFont(QFont("Courier New", 10))
        self._pid.setFixedHeight(30)
        self._pid.setStyleSheet(self._fs)
        self._pid.setPlaceholderText("a short name, e.g. freellm")
        form.addWidget(self._pid)

        self._base_url = self._field(form, "BASE URL", cfg.get("base_url") or "",
                                     "https://api.example.com/v1")
        self._api_key = self._field(form, "API KEY", cfg.get("api_key") or "",
                                    "sk-...", password=True)
        # Models as chips: one key, many models. The stored list lives in
        # "models"; "model" is the one the ladder currently uses.
        form.addWidget(self._lbl("MODEL  —  tekan ENTER untuk menambah, "
                                 "bisa lebih dari satu", 8, color=C.TEXT_DIM))
        _initial_models = list(cfg.get("models") or [])
        if not _initial_models and cfg.get("model"):
            _initial_models = [str(cfg["model"])]
        self._active_model = str(cfg.get("model") or "")
        self._models = _ModelChips(_initial_models)
        form.addWidget(self._models)
        wall = cfg.get("wall_s") or 0
        self._wall = self._field(
            form, "WALL (SECONDS) — HOW LONG TO LET IT WORK",
            str(wall) if wall else "",
            "0 = automatic (20s floor; caller's patience above it)")

        self._vision = self._toggle(form, "THIS MODEL CAN READ IMAGES",
                                    bool(cfg.get("vision")))
        tiers = cfg.get("tiers") or ["smart"]
        if isinstance(tiers, str):
            tiers = [tiers]
        tiers = {str(t).lower() for t in tiers}
        self._t_fast = self._toggle(form, "SERVE THE FAST TIER", "fast" in tiers)
        self._t_smart = self._toggle(
            form, "SERVE THE SMART TIER (CODING & HEAVY TASKS)",
            "smart" in tiers or not tiers)
        self._t_first = self._toggle(
            form, "LEAD THE LADDER (GEMINI ONLY AS FALLBACK)",
            get_provider_first())

        form.addSpacing(4)
        self._status = self._lbl(
            "No provider configured — Gemini handles every task."
            if not ids else f"Configured: {', '.join(ids)}.",
            8, color=C.TEXT_DIM)
        form.addWidget(self._status)
        form.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        test_btn = QPushButton("▸  TEST")
        test_btn.setFixedHeight(34)
        test_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        test_btn.setStyleSheet(f"""
            QPushButton {{ background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        test_btn.clicked.connect(self._run_test)
        btn_row.addWidget(test_btn)

        save_btn = QPushButton("▸  SAVE")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers (same conventions as ConnectorDialog) ────────────────────────

    def _lbl(self, txt, fs=9, bold=False, color=C.PRI):
        w = QLabel(txt); w.setWordWrap(True)
        w.setFont(QFont("Courier New", fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _field(self, form: QVBoxLayout, label: str, value: str,
               placeholder: str, password: bool = False) -> QLineEdit:
        form.addWidget(self._lbl(label, 8, color=C.TEXT_DIM))
        w = QLineEdit(value or "")
        w.setFont(QFont("Courier New", 10))
        w.setFixedHeight(30)
        w.setStyleSheet(self._fs)
        w.setPlaceholderText(placeholder)
        if password:
            w.setEchoMode(QLineEdit.EchoMode.Password)
        form.addWidget(w)
        return w

    def _toggle(self, form: QVBoxLayout, label: str, on: bool) -> QPushButton:
        w = QPushButton()
        w.setCheckable(True)
        w.setChecked(bool(on))
        w.setFixedHeight(28)
        w.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        w.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_toggle(w)
        w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
        form.addWidget(self._lbl(label, 8, color=C.TEXT_DIM))
        form.addWidget(w)
        return w

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    # ── test ──────────────────────────────────────────────────────────────────

    def _run_test(self):
        """A real, tiny request to the provider — the values currently in
        the fields, saved or not — so connectivity is proven before saving."""
        base = self._base_url.text().strip()
        key = self._api_key.text().strip()
        model = (self._models.chips() or [""])[0]
        pid = self._pid.text().strip() or "freellm"
        if not base or not model:
            self._status.setText("Fill in BASE URL and at least one MODEL first.")
            self._status.setStyleSheet("color: #ff6b6b; background: transparent;")
            return
        # Test with the same patience the ladder would give it — otherwise a
        # provider that is merely slow-but-alive (an aggregator re-routing)
        # fails a connectivity test it would have passed.
        try:
            wall = int(float(self._wall.text().strip() or 0))
        except ValueError:
            wall = 0
        self._status.setText("Testing…")
        self._status.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                from providers.llm_openai import OpenAILLM
                llm = OpenAILLM(pid, {"base_url": base, "api_key": key,
                                      "model": model})
                text, _ = llm.complete(
                    [{"role": "user", "content": "Reply with exactly: OK"}],
                    timeout=max(25.0, float(wall)))
                ok = bool(text)
                msg = (f"Connected — the model replied: {text[:60]!r}" if ok
                       else "Connected, but the model returned nothing.")
            except Exception as e:
                ok, msg = False, f"Failed: {str(e)[:160]}"
            self._test_done.emit(ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ok: bool, msg: str):
        self._status.setText(msg)
        self._status.setStyleSheet(
            f"color: {C.PRI if ok else '#ff6b6b'}; background: transparent;")

    # ── save ──────────────────────────────────────────────────────────────────

    def _save(self):
        from memory.config_manager import (
            save_provider_config, save_provider_first,
        )
        pid = self._pid.text().strip()
        if not pid:
            # A new gateway needs a name of its own — silently saving as
            # "freellm" would overwrite whatever is already under that id.
            self._status.setText("Isi PROVIDER ID dulu (nama singkat, mis. freellm).")
            self._status.setStyleSheet("color: #ff6b6b; background: transparent;")
            return
        base = self._base_url.text().strip()
        models = self._models.chips()
        if not base or not models:
            self._status.setText("Fill in BASE URL and at least one MODEL first.")
            self._status.setStyleSheet("color: #ff6b6b; background: transparent;")
            return
        # The active model stays where it was if it survived the edit;
        # otherwise the first chip takes over. The whole list is stored so
        # the board can offer a switch without retyping the key.
        active = self._active_model if self._active_model in models else models[0]
        tiers = [t for t, w in (("fast", self._t_fast), ("smart", self._t_smart))
                 if w.isChecked()]
        # Patience for this provider: seconds the ladder waits before it
        # decides the provider is stuck and moves to Gemini. An aggregator
        # on `model: auto` re-routing between upstreams is working, not
        # stuck — this is where that gets believed. 0 = automatic.
        try:
            wall = int(float(self._wall.text().strip() or 0))
        except ValueError:
            wall = 0
        try:
            save_provider_config(pid, {
                "base_url": base,
                "api_key":  self._api_key.text().strip(),
                "model":    active,
                "models":   models,
                "vision":   self._vision.isChecked(),
                "tiers":    tiers or ["smart"],
                "wall_s":   max(0, wall),
            })
            save_provider_first(self._t_first.isChecked())
            self._status.setText(
                f"Saved — '{pid}' is on the ladder. Heavy tasks route "
                f"through it on the next call.")
            self._status.setStyleSheet(f"color: {C.PRI}; background: transparent;")
            self.saved.emit(pid)
        except Exception as e:
            self._status.setText(f"Save failed: {e}")
            self._status.setStyleSheet("color: #ff6b6b; background: transparent;")


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class _ElideLabel(QLabel):
    """QLabel yang memendekkan teksnya sendiri (elipsis) saat ruangnya sempit.

    Kenapa perlu: QLabel biasa melaporkan lebar minimum sebesar teks penuhnya.
    Di strip bawah, kalimat "riwayat & profil tersimpan di perangkat ini"
    membuat lebar minimum jendela terkunci di 1215 px — jendela menolak
    dikecilkan, dan saat sempit teksnya terpotong tanpa elipsis. Label ini
    melaporkan lebar minimum 0 (boleh mengalah) tapi tetap minta lebar penuh
    bila ruang ada, lalu menggambar versi terpendek yang muat.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self._shown = ""
        self.setText(text)

    def setText(self, text):                      # noqa: N802 (API Qt)
        self._full = "" if text is None else str(text)
        self._shown = self._full
        super().setText(self._full)
        self.updateGeometry()
        self._elide()

    def fullText(self) -> str:
        return self._full

    def sizeHint(self):                           # noqa: N802
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._full), fm.height())

    def minimumSizeHint(self):                    # noqa: N802
        return QSize(0, self.fontMetrics().height())

    def resizeEvent(self, ev):                    # noqa: N802
        super().resizeEvent(ev)
        self._elide()

    def setFont(self, font):                      # noqa: N802
        super().setFont(font)
        self.updateGeometry()
        self._elide()

    def _elide(self) -> None:
        w = self.width()
        teks = (self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, w) if w > 0 else self._full)
        if teks != self._shown:
            self._shown = teks
            super().setText(teks)


class _FooterStrip(QWidget):
    """Strip bawah yang memberi tahu induknya setiap kali lebarnya berubah.

    Kenapa subclass kecil, bukan MainWindow.resizeEvent: ukuran huruf footer
    bergantung pada lebar STRIP (bukan lebar jendela), dan lebar strip baru
    pasti saat layout selesai. resizeEvent milik strip sendiri adalah satu-
    satunya titik yang dijamin membawa angka yang benar.
    """

    def __init__(self, on_resize, parent=None):
        super().__init__(parent)
        self._on_resize = on_resize

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        try:
            self._on_resize(self.width())
        except Exception:                    # noqa: BLE001
            pass                             # jangan pernah matikan Qt karenanya


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _model_sig      = pyqtSignal(str)        # (model name) — header badge, thread-safe
    _task_sig       = pyqtSignal(str)        # one-line custom-brain progress, thread-safe
    # Live Task Graph: (label, status ok|run|wait, ms). Thread-safe — the
    # brain and the job runner live on other threads, and touching a widget
    # from there is what crashed Qt before (see sonde_uithread).
    _tg_sig         = pyqtSignal(str, str, float)
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _screen_show_sig  = pyqtSignal(bool)          # share-screen panel on/off
    _screen_frame_sig = pyqtSignal(bytes, str)    # (jpeg, hint) → panel
    _video_open_sig  = pyqtSignal(str, str, bool, str)  # video, title, muted, audio
    _wake_btns_sig   = pyqtSignal()          # wake state resolved off-thread
    _video_close_sig = pyqtSignal()
    _video_mute_sig  = pyqtSignal(bool)
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload
    # ── masuk dari thread NON-Qt (loop asyncio aplikasi, callback audio) ────
    # Aplikasi menjalankan Qt di main thread sedangkan JarvisLive.run() hidup
    # di thread asyncio sendiri, dan callback sounddevice di thread ketiga.
    # Semua yang mereka kirim ke UI HARUS lewat sinyal ini: memanggil metode
    # widget langsung dari thread itu memunculkan warning
    # "QObject::setParent: Cannot set parent, new parent is in a different
    # thread" berulang kali dan berakhir dengan access violation (exit
    # -1073741819) di dalam QApplication.exec() — crash native tanpa traceback.
    _services_sig   = pyqtSignal(object, object, object, object)  # papan status + kartu TOOLS
    _refresh_ui_sig = pyqtSignal()           # gambar ulang model picker + kartu models
    _phone_sig      = pyqtSignal()           # overlay remote "tersambung"
    _mute_sig       = pyqtSignal(bool)       # bisu/bunyi dari thread mana pun

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "JARVIS").strip()
        _display = self._assistant_name.upper()

        # Apply the saved UI colour BEFORE panels/stylesheets are built
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by JarvisLive
        # MCP servers, for the CONNECTIONS panel — all four set by JarvisLive.
        self.get_mcp_servers    = None  # () -> list[dict] (id, enabled, alive, tools, cfg)
        self.save_mcp_server    = None  # (sid, cfg) -> '' or an error message
        self.delete_mcp_server  = None  # (sid) -> '' or an error message
        self.restart_mcp_server = None  # (sid) -> a human status line
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None

        central = QWidget()
        # The whole window sits on a soft vertical wash rather than one flat
        # colour: lighter at the top where the header glow falls, near-black
        # at the bottom. Scoped to #centralRoot on purpose — a bare QWidget
        # selector would repaint every child that has no style of its own.
        central.setObjectName("centralRoot")
        central.setStyleSheet("""
            QWidget#centralRoot {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #0a2033, stop:0.45 #061525, stop:1 #040e1a);
            }""")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # ── Video surface ────────────────────────────────────────────────
        # Built exactly like the camera page above and stacked beside it,
        # because it is the same idea: something takes the centre of the HUD
        # for a while and then gives it back. Sharing the stack rather than
        # inventing a second mechanism means the avatar, the camera and a video
        # can never be on screen at once.
        self._video_split = False        # is the sound a separate stream?
        self._video_auto_muted = False   # did JARVIS close the mic, or the user?
        # A plain flag rather than reading the widget. video_is_playing() is
        # called from plugin threads, and reading a widget's state from one is
        # not something to rely on; an attribute is.
        self._video_on = False
        self._video_cont = QWidget()
        self._video_cont.setStyleSheet(f"background: {C.BG};")
        _vid_v = QVBoxLayout(self._video_cont)
        _vid_v.setContentsMargins(8, 6, 8, 8)
        _vid_v.setSpacing(4)

        _vid_hdr = QHBoxLayout()
        self._video_title = QLabel("▶  VIDEO")
        self._video_title.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._video_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _vid_hdr.addWidget(self._video_title)
        _vid_hdr.addStretch()

        def _vid_btn(text: str) -> QPushButton:
            b = QPushButton(text)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_DIM}; background: transparent;
                    border: none; padding: 2px 6px;
                }}
                QPushButton:hover {{ color: {C.PRI}; }}
            """)
            return b

        # Muted is the default and the button says so, because a soundtrack
        # talking over JARVIS is the one way this feature could make the
        # assistant worse rather than better.
        self._video_mute_btn = _vid_btn("🔇  SOUND OFF")
        self._video_mute_btn.clicked.connect(self._toggle_video_mute)
        _vid_hdr.addWidget(self._video_mute_btn)

        _vid_x = _vid_btn("✕  CLOSE")
        _vid_x.clicked.connect(self.stop_video)
        _vid_hdr.addWidget(_vid_x)
        _vid_v.addLayout(_vid_hdr)

        if HAVE_VIDEO:
            # A GRAPHICS ITEM, NOT A QVideoWidget.
            #
            # QVideoWidget gets a native window of its own, and a native window
            # is painted by the compositor above every ordinary widget that
            # shares the screen with it — so the settings drawer opened
            # *underneath* the video and raise_() could not help, because the
            # two are not in the same painting order at all. A video item drawn
            # into a QGraphicsView goes through Qt's own painter like any other
            # widget, and anything laid over it stays over it.
            self._video_scene = QGraphicsScene(self)
            self._video_item = QGraphicsVideoItem()
            self._video_scene.addItem(self._video_item)
            self._video_widget = QGraphicsView(self._video_scene)
            self._video_widget.setStyleSheet("background: #000; border: none;")
            self._video_widget.setFrameShape(QGraphicsView.Shape.NoFrame)
            self._video_widget.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            _vid_v.addWidget(self._video_widget, stretch=1)

            self._video_audio = QAudioOutput()
            self._video_audio.setMuted(True)
            self._video_player = QMediaPlayer()
            self._video_player.setVideoOutput(self._video_item)
            # Keep the picture filling the view as the window is resized.
            self._video_item.nativeSizeChanged.connect(self._fit_video)
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.errorOccurred.connect(self._on_video_error)

            # A SECOND player, for sound that arrives separately.
            #
            # YouTube no longer serves a single stream carrying both picture and
            # sound — not for new uploads and not for old ones; checked against
            # three videos including the oldest on the site, and every one of
            # them offered zero combined formats. Picture and sound are two
            # URLs, so they are two players, started together and nudged back
            # into line by the timer below.
            #
            # A local file or a direct video URL still uses the first player
            # alone: one stream, one player, nothing to synchronise.
            self._video_sound = QMediaPlayer()
            self._video_sound_out = QAudioOutput()
            self._video_sound_out.setMuted(True)
            self._video_sound.setAudioOutput(self._video_sound_out)

            self._video_sync = QTimer(self)
            self._video_sync.setInterval(1000)
            self._video_sync.timeout.connect(self._sync_video_sound)
        else:
            self._video_scene = None
            self._video_item = None
            self._video_widget = None
            self._video_player = None
            self._video_audio = None
            self._video_sound = None
            self._video_sound_out = None
            self._video_sync = None
            _miss = QLabel("Video playback is not available in this Qt install.")
            _miss.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _miss.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            _vid_v.addWidget(_miss, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera, 2 = video
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)
        self._hud_cam_stack.addWidget(self._video_cont)

        # ── live screen share: Jarvis watches and coaches, never clicks ────
        _screen_cont = QWidget()
        _screen_cont.setStyleSheet("background: #000308;")
        _sv = QVBoxLayout(_screen_cont)
        _sv.setContentsMargins(0, 0, 0, 0)
        _sv.setSpacing(0)
        _sh = QHBoxLayout()
        _sh.setContentsMargins(8, 5, 8, 5)
        _screen_title = QLabel("◈  SHARE SCREEN — JARVIS MELIHAT & MEMBIMBING")
        _screen_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _screen_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _sh.addWidget(_screen_title)
        _sh.addStretch()
        _screen_stop = QPushButton("✕  STOP")
        _screen_stop.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _screen_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        _screen_stop.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _screen_stop.clicked.connect(self._request_screen_stop)
        _sh.addWidget(_screen_stop)
        _sv.addLayout(_sh)

        self._screen_live_lbl = QLabel()
        self._screen_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._screen_live_lbl.setStyleSheet("background: transparent;")
        self._screen_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        _sv.addWidget(self._screen_live_lbl, stretch=1)

        self._screen_hint_lbl = QLabel("Menunggu petunjuk…")
        self._screen_hint_lbl.setWordWrap(True)
        self._screen_hint_lbl.setFont(QFont("Courier New", 9))
        self._screen_hint_lbl.setStyleSheet(
            f"color: {C.PRI}; background: rgba(0,10,20,205); padding: 8px;")
        _sv.addWidget(self._screen_hint_lbl)

        self._hud_cam_stack.addWidget(_screen_cont)
        self._screen_page = self._hud_cam_stack.count() - 1

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)

        # ── SYSTEM STATUS card — the reference pins one on the stage's left:
        # ring gauges plus a network readout. Here it is a proper column
        # beside the HUD stack inside the splitter's first pane (the orbit
        # fills the rest of the width), fed by the same metric sampler.
        _stage = QWidget()
        _st = QHBoxLayout(_stage)
        _st.setContentsMargins(10, 10, 0, 0)
        _st.setSpacing(0)
        _stage_col = QVBoxLayout()
        _stage_col.setContentsMargins(0, 0, 0, 0)
        _stage_col.setSpacing(8)
        self._sys_status = SystemStatusPanel()
        self._task_graph = TaskGraph()
        self._task_graph.setFixedHeight(132)
        _stage_col.addWidget(self._sys_status, stretch=1)
        _stage_col.addWidget(self._task_graph)
        _stage_w = QWidget()
        _stage_w.setLayout(_stage_col)
        _st.addWidget(_stage_w, stretch=0)
        _st.addWidget(self._hud_cam_stack, stretch=1)

        self._center_split.addWidget(_stage)          # pane 0 = card + stage
        self._center_split.addWidget(self._content_panel)
        self._center_split.addWidget(self._quiz_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)

        _center_col = QWidget()
        _cl = QVBoxLayout(_center_col)
        _cl.setContentsMargins(0, 0, 0, 0)
        _cl.setSpacing(0)
        _cl.addWidget(self._center_split, stretch=1)
        _cl.addWidget(self._build_center_input())
        self._stage_w = _stage_w        # kept: HUD-axis alignment anchor

        # ── the body's shape, as measured off the second reference ────────
        #
        # The new reference runs the right rail the FULL body height and
        # puts the bottom cards inside the centre column (between sidebar
        # and rail): face + ask bar on top, three cards under them. The
        # sidebar stays the only full-height column on the left; the rail is
        # the full-height column on the right.
        self._right_panel = self._build_right_panel()

        _center_col2 = QWidget()
        _cc2 = QVBoxLayout(_center_col2)
        _cc2.setContentsMargins(0, 0, 0, 0)
        _cc2.setSpacing(0)
        _cc2.addWidget(_center_col, stretch=1)
        self._bottom_cards = self._build_bottom_cards()
        _cc2.addWidget(self._bottom_cards)

        body.addWidget(_center_col2, stretch=1)
        body.addWidget(self._right_panel, stretch=0)
        # The reference leaves ~18 px of floor under the cards before the
        # footer line.
        _mcl_spacer = 18

        root.addLayout(body, stretch=1)
        root.addSpacing(_mcl_spacer)
        root.addWidget(self._build_footer())

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._ctrl_drawer = self._build_controls_drawer()
        self._warm_wake_state()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # First paint of the two things that are read from state rather than
        # handed in: the footer's mouth and the safety card's rows.
        self._refresh_footer_voice()
        self._refresh_safety_card()
        self._refresh_model_picker()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        # The same line, kept in a short ring buffer so the RECENT RUNS card
        # is the log's tail rather than a second, drifting account of it.
        self._log_sig.connect(self._record_run)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._model_sig.connect(self._apply_model)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._screen_show_sig.connect(self._on_screen_show)
        self._screen_frame_sig.connect(self._on_screen_frame)
        self._wake_btns_sig.connect(self._refresh_wake_btns)
        self._video_open_sig.connect(self._on_video_open)
        self._video_close_sig.connect(self._on_video_close)
        self._video_mute_sig.connect(self._on_video_mute)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._task_sig.connect(self._apply_task_progress)
        self._tg_sig.connect(self._apply_task_graph)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._services_sig.connect(self._apply_services)
        self._refresh_ui_sig.connect(self._apply_refresh_ui)
        self._phone_sig.connect(self.notify_phone_connected)
        self._mute_sig.connect(self._apply_mute)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    # ── share screen: preview + coaching line ───────────────────────────────

    def _on_screen_show(self, on: bool) -> None:
        try:
            if on:
                self._hud_cam_stack.setCurrentIndex(self._screen_page)
                self._screen_hint_lbl.setText("Melihat layar…")
            else:
                self._hud_cam_stack.setCurrentIndex(0)
                self._screen_live_lbl.clear()
                self._screen_hint_lbl.setText("Menunggu petunjuk…")
        except Exception:
            pass

    def _on_screen_frame(self, jpeg: bytes, hint: str) -> None:
        px = QPixmap()
        px.loadFromData(jpeg)
        if not px.isNull():
            w, h = self._screen_live_lbl.width(), self._screen_live_lbl.height()
            if w > 1 and h > 1:
                # Fast scaling, not smooth: at ~12 fps the smooth filter costs
                # more than it shows, and it runs on the GUI thread.
                self._screen_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.FastTransformation))
        if hint:
            self._screen_hint_lbl.setText(str(hint))

    def _request_screen_stop(self) -> None:
        """STOP in the panel asks main.py to end the session (one source of
        truth: the loop that captures is the one that decides)."""
        cb = getattr(self, "on_screen_share", None)
        if callable(cb):
            try:
                cb(False)
            except Exception:
                pass

    def _toggle_screen_share(self) -> None:
        self._screen_on = not getattr(self, "_screen_on", False)
        try:
            self._share_btn.setText("◈ SHARE ●" if self._screen_on
                                    else "◈ SHARE")
        except Exception:
            pass
        cb = getattr(self, "on_screen_share", None)
        if callable(cb):
            try:
                cb(self._screen_on)
            except Exception:
                pass

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # --- Video in the HUD area ---------------------------------------------
    #
    # Everything below runs on the Qt thread. Plugins and the assistant reach it
    # through the signals above, the same way every other panel is driven, so a
    # background thread never touches a widget.
    def _on_video_open(self, source: str, title: str, muted: bool,
                       audio_source: str = "") -> None:
        if not HAVE_VIDEO or not self._video_player:
            self.write_log("SYS: Video playback is not available in this Qt "
                           "install.")
            return
        # A video and the live camera cannot share the centre of the HUD.
        self._cam_stop.set()

        self._video_title.setText(f"▶  {(title or 'VIDEO')[:44].upper()}")
        self._video_split = bool(audio_source)
        self._set_video_muted(bool(muted))

        url = (QUrl.fromLocalFile(source) if Path(source).exists()
               else QUrl(source))
        self._video_player.setSource(url)
        if self._video_split:
            self._video_sound.setSource(QUrl(audio_source))
        self._hud_cam_stack.setCurrentIndex(2)
        self._video_on = True
        # Re-run now that the video counts as playing: _set_video_muted ran
        # before this line and saw no video, so its mic check was a no-op.
        self._sync_mic_for_video()
        self._video_player.play()
        if self._video_split:
            self._video_sound.play()
            self._video_sync.start()

    def _fit_video(self, *_a) -> None:
        """Size the picture to the panel, keeping its shape."""
        if not (self._video_item and self._video_widget):
            return
        try:
            native = self._video_item.nativeSize()
            if native.isEmpty():
                return
            view = self._video_widget.viewport().size()
            scale = min(view.width() / native.width(),
                        view.height() / native.height())
            w, h = native.width() * scale, native.height() * scale
            self._video_item.setSize(QSizeF(w, h))
            self._video_scene.setSceneRect(0, 0, w, h)
            self._video_widget.centerOn(self._video_item)
        except Exception:
            pass

    def _on_video_close(self) -> None:
        if self._video_sync:
            self._video_sync.stop()
        for p in (self._video_player, self._video_sound):
            if p:
                p.stop()
                p.setSource(QUrl())
        self._video_split = False
        self._video_on = False
        self._hud_cam_stack.setCurrentIndex(0)
        self._sync_mic_for_video()      # gives the microphone back

    def _set_video_muted(self, muted: bool) -> None:
        """Silence whichever output is carrying the sound for this video."""
        muted = bool(muted)
        if self._video_audio:
            # When the sound is a separate stream this player has none, but
            # muting it too costs nothing and keeps the two paths identical.
            self._video_audio.setMuted(muted)
        if self._video_sound_out:
            self._video_sound_out.setMuted(muted)
        self._sync_video_mute_btn()
        self._sync_mic_for_video()

    def _on_video_mute(self, muted: bool) -> None:
        self._set_video_muted(muted)

    def _sync_mic_for_video(self) -> None:
        """Close the microphone while the video is making sound.

        JARVIS subtracts its OWN output from the microphone — that is what
        core/echo.py does — but a video plays through a different output
        entirely, so the guard has never heard of it and the assistant answers
        the film. There is no arrangement in which an open microphone and a
        loudspeaker in the same room do not do that.

        So the microphone closes for exactly as long as the sound is on, and
        opens again by itself the moment it goes off or the video is closed.
        Only if JARVIS closed it: a microphone the user muted themselves stays
        muted, and pressing the mute key during a video hands the decision back
        to them for good.
        """
        sound_on = bool(self._video_on and self._video_sound_out
                        and not self._video_sound_out.isMuted())
        if sound_on and not self._muted:
            self._video_auto_muted = True
            self._set_muted(True, "The video's sound is on — silence it, close "
                                  "it, or press F4 to talk. Typing still works.")
        elif not sound_on and self._video_auto_muted:
            self._video_auto_muted = False
            self._set_muted(False, "The video is quiet again.")

    def _sync_video_sound(self) -> None:
        """Keep the separate soundtrack in step with the picture.

        Two players started at the same moment do not stay together: they buffer
        independently, and measured on a real stream they were about half a
        second apart after nine seconds. So the sound is nudged back to the
        picture whenever it drifts far enough to hear — and only then, because
        correcting a smaller gap is itself audible.
        """
        if not (self._video_split and self._video_sound and self._video_player):
            return
        try:
            if self._video_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            drift = self._video_sound.position() - self._video_player.position()
            if abs(drift) > 300:
                self._video_sound.setPosition(self._video_player.position())
        except Exception:
            pass

    def _sync_video_mute_btn(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        muted = bool(out and out.isMuted())
        self._video_mute_btn.setText("🔇  SOUND OFF" if muted else "🔊  SOUND ON")

    def _toggle_video_mute(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        if out:
            self._set_video_muted(not out.isMuted())

    def _on_video_error(self, *_a) -> None:
        err = ""
        try:
            err = self._video_player.errorString()
        except Exception:
            pass
        self.write_log(f"SYS: The video could not be played{(' — ' + err) if err else ''}.")
        self._on_video_close()

    def stop_video(self) -> None:
        self._video_close_sig.emit()

    def video_is_playing(self) -> bool:
        return bool(self._video_on)

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()
        if hasattr(self, '_ctrl_drawer') and self._ctrl_drawer.isVisible():
            self._position_ctrl_drawer()
        self._fit_video()
        self._apply_proportions()

    def _update_metrics(self):
        """One sample, one board.

        The sidebar's bars moved to the right-hand status board, so this
        timer now has exactly one place to paint — no duplicated gauges, no
        stale copy of a number on the other side of the window.
        """
        snap = _metrics.snapshot()
        try:
            self._sys_status.refresh(snap)     # the stage's SYSTEM STATUS card
        except Exception:                      # noqa: BLE001
            pass
        try:
            self._refresh_qc_subs()            # quick-control sub-labels
        except Exception:                      # noqa: BLE001
            pass
        try:
            self._refresh_vitals()             # LATENSI/TOKS/KONTEKS/SHIELD chips
        except Exception:                      # noqa: BLE001
            pass

    # ── proportional layout (matches the JARVIS Home mockup grid) ────────────
    # The mockup's grid is 230px | 1fr | 340px with a 68px top row. Below
    # ~1200px wide the fixed 230/340 would squeeze the stage, so the columns
    # scale with the window but keep the mockup's ratio (230:340) and only
    # loosen at the extremes; the card strip keeps its height clamp.
    _P_LEFT, _P_RIGHT, _P_CARDS = 0.150, 0.221, 0.240   # 230/1536, 340/1536

    def _apply_proportions(self):
        try:
            w, h = self.width(), self.height()
            if w <= 200 or h <= 200:
                return
            if w >= 1200:
                left, right = _LEFT_W, _RIGHT_W       # the mockup's own pixels
            else:
                left = max(168, min(260, int(w * self._P_LEFT)))
                right = max(260, min(420, int(w * self._P_RIGHT)))
            cards = max(200, min(420, int(h * self._P_CARDS)))
            if self._left_panel.width() != left:
                self._left_panel.setFixedWidth(left)
            if self._right_panel.width() != right:
                self._right_panel.setFixedWidth(right)
            if getattr(self, "_bottom_cards", None) is not None \
                    and self._bottom_cards.height() != cards:
                self._bottom_cards.setFixedHeight(cards)
        except Exception:                      # noqa: BLE001
            pass
        self._align_hud_axis()

    def _align_hud_axis(self) -> None:
        """One paint axis for the HUD and the command row beneath it.

        The pill + chips row is centred across the whole centre column,
        while the HUD canvas only spans the area right of the status
        column — its raw centre is therefore about half a status-column
        too far right, which read as a skew. axis_shift (consumed by
        HudCanvas.paintEvent) puts head/orbit/status/waveform back on the
        command row's optical axis.
        """
        try:
            hud = self.hud
            stage_w = getattr(self, "_stage_w", None)
            if stage_w is None:
                hud.axis_shift = 0.0
                return
            hud_left_global = hud.mapTo(self, hud.rect().topLeft()).x()
            cmd_centre_global = self._center_split.mapTo(
                self, self._center_split.rect().topLeft()).x() \
                + self._center_split.width() // 2
            hud_w = hud.width() or 1
            cmd_centre_local = cmd_centre_global - hud_left_global
            shift = cmd_centre_local - hud_w / 2.0
            if getattr(hud, "axis_shift", 0.0) != shift:
                hud.axis_shift = shift
                hud.update()
        except Exception:                      # noqa: BLE001
            pass

    def _build_header(self) -> QWidget:
        """The top bar, arranged like the reference's.

        Reading left to right: the brand, then what the app is (a private,
        local-memory assistant), then the brain the heavy work goes to, then
        the box that searches everything the app holds, then the clock, the
        two drawers and the live model badge. The brand used to be centred
        with nothing either side of it; the reference leads with it, and a
        header that leads with the name reads faster than one that centres it.
        """
        w = QWidget()
        w.setFixedHeight(68)   # mockup .top row: 68px
        self._header = w       # kept for probes/layout math
        w.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #050f1d, stop:0.50 #0a1c33, stop:1 #050f1d);"
            f" border-bottom: 1px solid {C.BORDER_B};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(18, 0, 14, 0)
        lay.setSpacing(0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(_ui_font(10))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        # ── brand ────────────────────────────────────────────────────────
        # Mockup .logo: a 38px circle, 3px cyan ring, outer glow + inner core.
        mark = QLabel()
        mark.setFixedSize(38, 38)
        mark.setStyleSheet(f"""
            QLabel {{
                background: qradialgradient(cx:0.5, cy:0.5, radius:0.55,
                    stop:0 {C.PRI}, stop:0.55 {C.BORDER_B}, stop:1 {C.DARK});
                border: 3px solid {C.PRI}; border-radius: 19px;
            }}""")
        lay.addWidget(mark)
        lay.addSpacing(12)

        brand = QVBoxLayout(); brand.setSpacing(0)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        # Mockup .brand: Chakra Petch 700. It falls back to Consolas/Courier
        # when the font isn't installed; _hud_fonts() installs it when found.
        self._title_lbl.setFont(_display_font(17, bold=True))
        self._title_lbl.setStyleSheet(
            f"color: {C.WHITE}; background: transparent; letter-spacing: 5px;")
        brand.addWidget(self._title_lbl)
        self._sub_lbl = QLabel("LOCAL AI ASSISTANT")
        self._sub_lbl.setFont(QFont("Courier New", 7))
        self._sub_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; letter-spacing: 2px;")
        brand.addWidget(self._sub_lbl)
        lay.addLayout(brand)
        lay.addSpacing(14)

        _sep = QFrame()
        _sep.setFrameShape(QFrame.Shape.VLine)
        _sep.setFixedHeight(30)
        _sep.setStyleSheet(f"color: {C.BORDER_A};")
        lay.addWidget(_sep)
        lay.addSpacing(14)

        # ── what this is ─────────────────────────────────────────────────
        # The reference says "LOCAL · PRIVATE / Your data stays on your
        # device". JARVIS does send prompts to the model the user configured,
        # so the claim is narrowed to the part that is literally true: the
        # conversation and the memory stay on this machine.
        _pill = QWidget()
        pl = QHBoxLayout(_pill)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(7)
        _dot = QLabel("●")
        _dot.setFont(QFont("Courier New", 11))
        _dot.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        pl.addWidget(_dot)
        _ptext = QVBoxLayout(); _ptext.setSpacing(0)
        _p1 = QLabel("LOCAL MODE")
        _p1.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        _p1.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        _ptext.addWidget(_p1)
        self._sys_line = QLabel("All systems running locally")
        self._sys_line.setFont(QFont("Courier New", 7))
        self._sys_line.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        _ptext.addWidget(self._sys_line)
        pl.addLayout(_ptext)
        lay.addWidget(_pill)
        lay.addSpacing(16)

        # ── Privacy Shield ───────────────────────────────────────────────
        # The mockup's third chip, wired to the real privacy state: a click
        # toggles the shield, and the sub-line says what the shield covers.
        self._shield_btn = QPushButton()
        self._shield_btn.setFixedHeight(30)
        self._shield_btn.setFont(_ui_font(9, bold=True))
        self._shield_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shield_btn.setToolTip(
            "Privacy Shield: kunci API hanya di perangkat, wake word lokal, "
            "dan tangkapan layar/clipboard tidak dikirim ke mana pun tanpa "
            "perintahmu. Klik untuk on/off.")
        self._shield_btn.clicked.connect(self._toggle_privacy_shield)
        lay.addWidget(self._shield_btn)
        self._refresh_privacy_shield()
        lay.addSpacing(10)

        # commit-model chip goes next; _refresh_privacy_shield and the toggle
        # live at class level, just after _build_header returns.
        lay.addSpacing(4)

        self._model_pick_btn = QPushButton("⚡  memuat…  ▾")
        self._model_pick_btn.setFixedHeight(30)
        self._model_pick_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._model_pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._model_pick_btn.setStyleSheet(f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_B}; border-radius: 15px;
                padding: 0 12px; text-align: left; }}
            QPushButton:hover {{ border-color: {C.PRI_DIM}; background: {C.PRI_GHO}; }}""")
        self._model_pick_btn.setToolTip(
            "Otak untuk tugas berat (teks & kode). Gemini Live tetap menjawab "
            "percakapan suara dan hanya menjadi cadangan.")
        self._model_pick_btn.clicked.connect(self._show_model_menu)
        lay.addWidget(self._model_pick_btn)
        lay.addSpacing(10)

        # ── search everything the app holds ──────────────────────────────
        self._search = QLineEdit()
        self._search.setFixedHeight(30)
        self._search.setFixedWidth(250)
        self._search.setPlaceholderText("🔍  Cari berkas, perintah, alat…")
        self._search.setFont(QFont("Courier New", 9))
        self._search.setStyleSheet(f"""
            QLineEdit {{ background: {C.PANEL2}; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 15px;
                padding: 0 12px; }}
            QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}""")
        self._search.returnPressed.connect(
            lambda: self._search_everything(self._search.text()))
        lay.addWidget(self._search)

        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(0)
        # Mockup: small date line on top, big white display clock under it.
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Courier New", 8))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(_display_font(17, bold=True))
        self._clock_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        # Which brain answered last. The ladder can step between rungs (quota,
        # failures), so this is published per answer rather than fixed at
        # build time — Gemini while it talks, the custom API while it works.
        self._model_lbl = QLabel("AI MODEL: —")
        self._model_lbl.setFont(QFont("Courier New", 8))
        self._model_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._model_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._model_lbl)
        lay.addLayout(right_col)
        lay.addSpacing(10)

        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(26, 26)
        self._drawer_btn.setFont(QFont("Courier New", 11))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Controls")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.setToolTip("Setup — things you set once")
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)

        # A SECOND drawer, and the split is by how often a thing is touched.
        #
        # One panel held twelve buttons: the ones you press once when you set
        # the assistant up sitting next to the ones you flick on and off every
        # day. Separating them by that — setup behind ⚙, everyday behind 🎛 —
        # is what makes each list short enough to read at a glance.
        self._ctrl_btn = QPushButton("🎛")
        self._ctrl_btn.setFixedSize(26, 26)
        self._ctrl_btn.setFont(QFont("Courier New", 11))
        self._ctrl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ctrl_btn.setToolTip("Controls — the everyday switches")
        self._ctrl_btn.setStyleSheet(self._drawer_btn.styleSheet())
        self._ctrl_btn.setCheckable(True)
        self._ctrl_btn.clicked.connect(self._toggle_controls)
        lay.addSpacing(4)
        lay.addWidget(self._ctrl_btn)

        self._refresh_model_picker()
        return w

    def _refresh_privacy_shield(self) -> None:
        """Chip text/colour from the real privacy state."""
        try:
            from memory.config_manager import get_privacy_shield
            st = get_privacy_shield()
        except Exception:
            st = {"enabled": False, "screen_guard": False}
        on = bool(st.get("enabled"))
        col = "#3CF0A0" if on else C.TEXT_DIM
        self._shield_btn.setText(
            f"🛡 Privacy Shield · {'ON' if on else 'OFF'}")
        self._shield_btn.setStyleSheet(f"""
            QPushButton {{
                background: {'rgba(60,240,160,0.07)' if on else 'transparent'};
                color: {col};
                border: 1px solid {('#3cf0a073' if on else C.BORDER)};
                border-radius: 15px; padding: 0 12px;
            }}
            QPushButton:hover {{ border-color: {col}; }}""")

    def _toggle_privacy_shield(self) -> None:
        try:
            from memory.config_manager import (get_privacy_shield,
                                               save_privacy_shield)
            save_privacy_shield(not bool(get_privacy_shield().get("enabled")))
        except Exception as e:                  # noqa: BLE001
            self._log.append_log(f"ERR: privacy shield: {e}")
        self._refresh_privacy_shield()

    # ── the header's model picker ───────────────────────────────────────────

    def _current_heavy_brain(self) -> tuple[str, str]:
        """The provider + model the heavy-work ladder leads with, from config."""
        try:
            from memory.config_manager import (get_active_provider,
                                               get_provider_config,
                                               list_provider_ids)
            ids = list_provider_ids()
            if not ids:
                return "", ""
            pid = get_active_provider() or ids[0]
            return pid, str(get_provider_config(pid).get("model") or "")
        except Exception:
            return "", ""

    def _refresh_model_picker(self) -> None:
        btn = getattr(self, "_model_pick_btn", None)
        if btn is None:
            return
        pid, model = self._current_heavy_brain()
        if pid and model:
            label = model if len(model) <= 26 else model[:25] + "…"
            btn.setText(f"⚡  {label}  ▾")
        else:
            btn.setText("⚡  Gemini Live  ▾")

    def _show_model_menu(self) -> None:
        """Pick the heavy-work brain without leaving the HUD.

        One menu, one flat list, because the alternative — a board, then a
        card, then a model row — is three clicks to do what the reference
        does in one. Gemini is listed but not selectable: it is the voice
        brain and the silent fallback, never a rung the user switches to.
        """
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background: {C.DARK}; color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            QMenu::item {{ padding: 6px 22px; }}
            QMenu::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}
            QMenu::item:disabled {{ color: {C.TEXT_DIM}; }}
            QMenu::separator {{ height: 1px; background: {C.BORDER}; margin: 4px 8px; }}""")

        head = QAction("OTAK TUGAS BERAT", menu)
        head.setEnabled(False)
        menu.addAction(head)

        try:
            from memory.config_manager import (get_active_provider,
                                               get_provider_config,
                                               list_provider_ids)
            ids = list_provider_ids()
        except Exception:
            ids = []
        active_pid, active_model = self._current_heavy_brain()

        found = 0
        for pid in ids:
            cfg = get_provider_config(pid)
            models = [m for m in (cfg.get("models") or []) if str(m).strip()]
            if not models and cfg.get("model"):
                models = [cfg["model"]]
            for m in models:
                is_active = (pid == active_pid and str(m) == active_model)
                a = QAction(f"{'● ' if is_active else '   '}{m}   ·   {pid}", menu)
                a.setCheckable(True)
                a.setChecked(is_active)
                a.triggered.connect(
                    lambda _=False, p=pid, mm=str(m): self._set_heavy_model(p, mm))
                menu.addAction(a)
                found += 1
        if not found:
            none = QAction("(belum ada model custom)", menu)
            none.setEnabled(False)
            menu.addAction(none)

        menu.addSeparator()
        gem = QAction("◈  Gemini Live — suara & percakapan (tetap)", menu)
        gem.setEnabled(False)
        menu.addAction(gem)

        # ── engine mode: who does the thinking ──────────────────────────────
        # The key existed in config with no way to reach it from the interface;
        # the only route was editing api_keys.json by hand. Read per connect in
        # main.py, so the change lands on the next connection.
        menu.addSeparator()
        _mode_head = QAction("MODE MESIN", menu)
        _mode_head.setEnabled(False)
        menu.addAction(_mode_head)
        try:
            from memory.config_manager import load_api_keys as _lak
            _mode = str((_lak() or {}).get("voice_brain") or "live").strip().lower()
        except Exception:
            _mode = "live"
        for _key, _label in (("live", "Cepat — Gemini berpikir & berbicara"),
                             ("custom", "Hibrida — Gemini telinga/mulut, otak custom")):
            _a = QAction(("● " if _mode == _key else "   ") + _label, menu)
            _a.setCheckable(True)
            _a.setChecked(_mode == _key)
            _a.triggered.connect(
                lambda _=False, k=_key: self._set_engine_mode(k))
            menu.addAction(_a)

        menu.addSeparator()
        manage = QAction("＋  Tambah / kelola model…", menu)
        manage.triggered.connect(self._open_providers)
        menu.addAction(manage)

        menu.exec(self._model_pick_btn.mapToGlobal(
            QPoint(0, self._model_pick_btn.height() + 4)))

    def _set_heavy_model(self, pid: str, model: str) -> None:
        """Point the ladder at one model. Read live by core/gemini.py on the
        next call, so no restart and no reconnect."""
        try:
            from memory.config_manager import (save_active_provider,
                                               save_provider_config)
            save_active_provider(pid)
            save_provider_config(pid, {"model": model})
            self._log.append_log(f"SYS: otak custom → {pid} · {model}")
            self._refresh_model_picker()
            self._refresh_models_card()
        except Exception as e:                  # noqa: BLE001
            self._log.append_log(f"ERR: ganti model gagal: {e}")

    def _set_engine_mode(self, mode: str) -> None:
        """live | custom — who does the thinking.

        Written to config and read per connect, so the sentence says plainly
        that it lands on the next connection rather than pretending it is
        already in force.
        """
        try:
            from memory.config_manager import set_voice_brain
            set_voice_brain(mode)
            self._log.append_log(
                "SYS: mode mesin → "
                + ("HIBRIDA (Gemini telinga/mulut, otak custom)"
                   if mode == "custom" else "CEPAT (Gemini berpikir & berbicara)")
                + " — berlaku setelah sambung ulang.")
            self._refresh_model_picker()
        except Exception as e:                  # noqa: BLE001
            self._log.append_log(f"ERR: ganti mode mesin gagal: {e}")

    # ── the header's search box ─────────────────────────────────────────────

    def _search_everything(self, term: str) -> None:
        """Search what the app actually holds — no index, no guessing.

        Three real sources: the files JARVIS has been handed, the plugins and
        actions this run loaded, and the providers in config. Whatever does
        not match is reported as not matching, rather than filled in with
        something close.
        """
        term = (term or "").strip().lower()
        if not term:
            return
        hits: list[str] = []

        try:
            from core import recent
            for it in recent.items(12):
                if term in it["name"].lower() or term in it["path"].lower():
                    hits.append(f"[berkas]    {it['name']}  ·  {it['when']}\n"
                                f"            {it['path']}")
        except Exception:
            pass

        try:
            plugins = (self.get_plugins() if self.get_plugins else []) or []
        except Exception:
            plugins = []
        for p in plugins:
            name = str(p.get("name", ""))
            desc = str(p.get("description", ""))
            if term in name.lower() or term in desc.lower():
                hits.append(f"[plugin]    {name}\n            {desc[:110]}")

        try:
            acts = (self.get_actions() if getattr(self, "get_actions", None) else []) or []
        except Exception:
            acts = []
        for a in acts:
            name = str(a.get("name", ""))
            desc = str(a.get("description", ""))
            if term in name.lower() or term in desc.lower():
                hits.append(f"[aksi]      {name}\n            {desc[:110]}")

        try:
            from memory.config_manager import get_provider_config, list_provider_ids
            for pid in list_provider_ids():
                cfg = get_provider_config(pid)
                blob = " ".join(str(x) for x in
                                [pid, cfg.get("model", ""), cfg.get("base_url", "")]
                                + list(cfg.get("models") or []))
                if term in blob.lower():
                    hits.append(f"[model]     {pid}\n"
                                f"            {cfg.get('model', '—')}  ·  "
                                f"{cfg.get('base_url', '')}")
        except Exception:
            pass

        self._search.clear()
        if hits:
            self._show_content(f"PENCARIAN · {term}", "\n\n".join(hits[:30]))
            self._log.append_log(f"SYS: cari '{term}' → {len(hits)} hasil")
        else:
            self._show_content(
                f"PENCARIAN · {term}",
                f"Tidak ada yang cocok dengan “{term}”.\n\n"
                "Yang dicari: berkas yang pernah dikerjakan, plugin, aksi, "
                "dan model custom. Coba kata yang lebih pendek.")
            self._log.append_log(f"SYS: cari '{term}' → tidak ada hasil")

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        # Mockup format: "Sun 18 Okt 2026" — Indonesian month, brief.
        _ID_MO = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
                  "Jul", "Agu", "Sep", "Okt", "Nov", "Des")
        now = time.localtime()
        self._date_lbl.setText(
            f"{time.strftime('%a', now)} {now.tm_mday:02d} "
            f"{_ID_MO[now.tm_mon - 1]} {now.tm_year}")
        self._align_hud_axis()      # keep the HUD axis on the command axis

    def _build_left_panel(self) -> QWidget:
        """The sidebar: navigation, not telemetry.

        The reference dashboard puts navigation on the left and the numbers
        on the right; this HUD used to do the opposite. The bars moved to
        the status board (which is where the user asked for them), and this
        column became the way in to everything the app already has — each
        row opens a panel that exists, nothing here is a picture of a button.
        """
        w = QWidget()
        w.setMinimumWidth(168)
        w.setStyleSheet(f"background: {C.DARK}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 8, 6, 10)
        lay.setSpacing(2)

        # No wordmark here any more — it is in the header, where the reference
        # puts it. Leaving both would have printed the name twice on the same
        # screen, and the sidebar only has room for the things you press.

        self._nav_btns: list[QPushButton] = []
        for label, icon, handler in (
            ("Home",        "⌂", self._nav_home),
            ("Chat",        "💬", self._nav_chat),
            ("Agents",      "🤖", self._nav_tools),        # plugins+actions inventory
            ("Workflows",   "scientist", self._nav_workflows),
            ("Files",       "📁", self._nav_files),
            ("Tools",       "🛠", self._nav_tools),
            ("Memory",      "🧠", self._open_memory_panel),
            ("Plugins",     "🧩", self._open_plugin_manager),
            ("Connections", "🔗", self._open_connections),
            ("AI Provider", "⚡", self._open_providers),
            ("Settings",    "⚙", self._open_customize),
        ):
            icon_txt = "⚗" if icon == "scientist" else icon   # workflows: flask
            b = QPushButton(f"  {icon_txt}   {label}")
            b.setMinimumHeight(44)
            b.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setCheckable(True)
            self._style_nav_btn(b)
            b.clicked.connect(
                lambda _=False, h=handler, idx=len(self._nav_btns):
                    self._nav_clicked(idx, h))
            self._nav_btns.append(b)
            lay.addWidget(b)

        lay.addStretch()

        # The reference closes its rail with a status bank (AI CORE active /
        # SEC cleared / PROTOCOL ARIS) and an Ambient row for focus mode.
        # Ours reads real state: the session gate for CORE, the mic for SEC,
        # the running assistant for PROTOCOL.
        _bank = QFrame()
        _bank.setStyleSheet(
            f"QFrame {{ background: {C.PANEL2};"
            f" border: 1px solid {C.BORDER_A}; border-radius: 6px; }}")
        bv = QVBoxLayout(_bank)
        bv.setContentsMargins(9, 8, 9, 8)
        bv.setSpacing(5)

        _cap = QLabel("AI CORE")
        _cap.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cap.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
        bv.addWidget(_cap)

        self._bank_core = self._bank_row(bv, "CORE", "Active")
        self._bank_sec  = self._bank_row(bv, "SEC",  "Cleared")
        self._bank_prot = self._bank_row(bv, "PROTOCOL", "ARIS")
        lay.addWidget(_bank)

        self._ambient_btn = QPushButton("♪   Ambient    Focus Mode  ›")
        self._ambient_btn.setFixedHeight(34)
        self._ambient_btn.setFont(QFont("Courier New", 9))
        self._ambient_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ambient_btn.setToolTip("Mode fokus: bisukan mic & redupkan animasi")
        self._ambient_btn.setStyleSheet(f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER_A}; border-radius: 6px;
                text-align: left; padding: 0 9px; }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}""")
        self._ambient_btn.clicked.connect(self._ambient_toggle)
        lay.addWidget(self._ambient_btn)

        self._nav_active = 0
        self._nav_clicked(0, None, run=False)
        return w

    def _bank_row(self, bv: QVBoxLayout, key: str, val: str) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(6)
        k = QLabel(key)
        k.setFont(QFont("Courier New", 7))
        k.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        v = QLabel(f"◆  {val}")
        v.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        v.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        row.addWidget(k)
        row.addStretch()
        row.addWidget(v)
        bv.addLayout(row)
        return v

    def _ambient_toggle(self) -> None:
        """Focus mode: mute the mic gate and dim the HUD's animations — the
        two switches the app really has for 'leave me alone for a bit'."""
        muted = not bool(getattr(self, "_muted", False))
        self._set_muted(muted, "Ambient / focus mode." if muted
                        else "Kembali ke mode penuh.")
        try:
            self.hud.dimmed = muted
            self.hud.update()
        except Exception:                       # noqa: BLE001
            pass
        self._ambient_btn.setText(
            "♪   Ambient    Fokus AKTIF  ›" if muted
            else "♪   Ambient    Focus Mode  ›")

    def _style_nav_btn(self, b: QPushButton) -> None:
        """One stylesheet, two states — the reference marks the page you are
        on with a filled row, and without a checked state every row looked
        equally current."""
        b.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid transparent; border-radius: 6px;
                text-align: left; padding: 0 12px; }}
            QPushButton:hover {{ color: {C.PRI}; background: {C.PRI_GHO};
                border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; background: {C.PRI_GHO};
                border-color: {C.PRI_DIM}; font-weight: bold; }}""")

    def _nav_clicked(self, idx: int, handler, run: bool = True) -> None:
        """Mark one row current, then do the thing that row does."""
        try:
            for i, b in enumerate(getattr(self, "_nav_btns", [])):
                b.setChecked(i == idx)
            self._nav_active = idx
        except Exception:                       # noqa: BLE001
            pass
        if run and handler is not None:
            handler()

    # ── sidebar actions ─────────────────────────────────────────────────────

    def _nav_home(self) -> None:
        """Back to the HUD: close the floating panels, keep the session."""
        try:
            self._toggle_drawer(False)
        except Exception:
            pass
        for attr in ("_providers_overlay", "_memory_overlay", "_connections_overlay",
                     "_plugin_manager_overlay", "_audio_overlay", "_provider_overlay"):
            ov = getattr(self, attr, None)
            if ov is not None:
                try:
                    ov.hide()
                except Exception:
                    pass
        self._log.append_log("SYS: Home — semua panel ditutup.")

    def _nav_chat(self) -> None:
        """Put the caret in the ask bar, where a conversation starts."""
        self._input_center.setFocus()
        self._log.append_log("SYS: Chat — ketik perintah atau tanya.")

    def _nav_files(self) -> None:
        """The file picker the drop zone already owns."""
        try:
            self._drop_zone._browse()
        except Exception as e:                  # noqa: BLE001
            self._log.append_log(f"ERR: file picker: {e}")

    def _nav_tools(self) -> None:
        """A read-only inventory of what this run actually loaded."""
        try:
            plugins = (self.get_plugins() if self.get_plugins else []) or []
            acts = (self.get_actions() if getattr(self, "get_actions", None) else []) or []
        except Exception:
            plugins, acts = [], []
        lines = [f"PLUGIN  ({len(plugins)})"]
        for p in plugins[:14]:
            lines.append(f"  • {p.get('name', '?')}")
        lines.append("")
        lines.append(f"ACTION  ({len(acts)})")
        for a in acts[:14]:
            lines.append(f"  • {a.get('name', '?')}")
        if not plugins and not acts:
            lines.append("(daftar belum tersedia — jalankan JARVIS penuh)")
        self._show_content("TOOLS & AGENTS", "\n".join(lines))

    def _nav_workflows(self) -> None:
        """Workflows: the automations this build actually has — the brief,
        the wake gate, the phone dashboard — plus what actions could chain."""
        try:
            acts = (self.get_actions() if getattr(self, "get_actions", None) else []) or []
        except Exception:
            acts = []
        lines = [
            "AUTOMASI AKTIF",
            "  • Briefing periodik — jadwal berita ringkas",
            "  • Wake word — panggil JARVIS lewat suara",
            "  • Dashboard HP — kontrol jarak jauh dari ponsel",
            "",
            f"ACTION YANG BISA DIRANGKAI  ({len(acts)})",
        ]
        for a in acts[:16]:
            lines.append(f"  • {a.get('name', '?')}")
        self._show_content("WORKFLOWS", "\n".join(lines))

    def _build_right_panel(self) -> QWidget:
        """The right rail, arranged like the second reference.

        Top to bottom: ACTIVITY LOG (the main event again — always visible,
        fed by the same signal as before), QUICK CONTROLS (2x2, each tile
        with a live sub-label), FILE UPLOAD, then COMMAND INPUT with the
        INTERRUPT button under it. Everything wires to the handlers that
        already existed; only the arrangement changed.
        """
        w = QWidget()
        w.setMinimumWidth(300)
        w.setStyleSheet(f"background: {C.DARK}; border-left: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        def _sec(txt, action: str | None = None, cb=None):
            hdr = QHBoxLayout()
            t = QLabel(txt)
            t.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
            t.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            hdr.addWidget(t)
            hdr.addStretch()
            if action and cb is not None:
                a = QPushButton(action)
                a.setFixedHeight(18)
                a.setFont(QFont("Courier New", 7))
                a.setCursor(Qt.CursorShape.PointingHandCursor)
                a.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.PRI};
                        border: none; }}
                    QPushButton:hover {{ color: {C.WHITE}; }}""")
                a.clicked.connect(cb)
                hdr.addWidget(a)
            lay.addLayout(hdr)

        # ── ACTIVITY LOG — the rail's main card, always on ──────────────
        _sec("ACTIVITY LOG", "Lihat semua →", self._show_activity_log)
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        # ── QUICK CONTROLS ───────────────────────────────────────────────
        _sec("QUICK CONTROLS")
        lay.addLayout(self._build_quick_controls())

        # ── FILE UPLOAD ──────────────────────────────────────────────────
        _sec("FILE UPLOAD")
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        # ── COMMAND INPUT + INTERRUPT (back, per the new reference) ─────
        _sec("COMMAND INPUT")
        lay.addLayout(self._build_input_row())

        self._interrupt_btn = QPushButton("✋  INTERRUPT  [ESC]")
        self._interrupt_btn.setFixedHeight(30)
        self._interrupt_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: #140008; color: {C.RED};
                border: 1px solid {C.RED}; border-radius: 4px;
            }}
            QPushButton:hover {{
                background: #200010; border: 1px solid #ff6688;
            }}
            QPushButton:pressed {{
                background: #300018;
            }}
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        lay.addWidget(self._interrupt_btn)

        return w

    def _build_input_row(self) -> QHBoxLayout:
        """The rail's own command box. It and the big centred ask bar share
        one send path (`_send`), so both stay true inputs, not echo copies."""
        row = QHBoxLayout(); row.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Courier New", 9))
        self._input.setFixedHeight(32)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 3px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input, 1)

        send = QPushButton("▸")
        send.setFixedSize(32, 32)
        send.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 16px;
            }}
            QPushButton:hover {{ background: {C.PRI}; color: #00080a; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def set_services(self, mcp_tools: int | None = None,
                     plugins: int | None = None,
                     brain: str | None = None,
                     wake: str | None = None) -> None:
        """Public: the app tells the status board what it loaded."""
        try:
            self._sys_status.set_services(mcp_tools=mcp_tools, plugins=plugins,
                                          brain=brain, wake=wake)
        except Exception:                      # noqa: BLE001
            pass
        try:
            self._refresh_tools_card()         # the bottom strip reads the same data
        except Exception:                      # noqa: BLE001
            pass

    # ── slot untuk sinyal dari thread non-Qt ───────────────────────────────
    #
    # Semua berjalan di thread Qt. Yang memanggil (loop asyncio aplikasi,
    # callback audio) hanya meng-emit sinyal — tidak pernah menyentuh widget
    # langsung;itu yang dulu menghasilkan crash access violation.

    def _apply_services(self, mcp_tools, plugins, brain, wake) -> None:
        self.set_services(mcp_tools=mcp_tools, plugins=plugins,
                          brain=brain, wake=wake)

    def _apply_refresh_ui(self) -> None:
        try:
            self._refresh_model_picker()
            self._refresh_models_card()
        except Exception:                      # noqa: BLE001
            pass

    def _apply_mute(self, on: bool) -> None:
        if bool(on) != self._muted:
            self._toggle_mute()

    # ── the centred ask bar (under the face, like the reference) ────────────

    def _build_center_input(self) -> QWidget:
        """The mockup's command area, wired to real behaviour:

        - vitals chips (LATENSI / tok/s / KONTEKS / RAHASIA DISAMARKAN) —
          measured from the session where the session knows it, hidden when
          it does not (no invented numbers);
        - the big pill input with ✦, placeholder "Tanya JARVIS apa saja…
          (Ctrl + K)" and Suara / Kirim buttons (Suara taps the same voice
          path the footer uses, Kirim is the rail-shared send path);
        - four quick chips — Analisis proyek, Cari berkas, Buat otomasi,
          Buka terminal — each one pre-fills the pill with a real command
          the assistant understands, and Enter still sends it. Chips are
          prompts waiting to be edited, not silent executors.
        """
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(24, 2, 24, 8)
        v.setSpacing(6)

        # ── vitals chips ────────────────────────────────────────────────
        self._vitals_row = QHBoxLayout()
        self._vitals_row.setSpacing(8)
        self._vitals_row.addStretch()
        self._vital_lbls: dict[str, QLabel] = {}
        for key in ("LATENSI", "TOKS", "KONTEKS", "SHIELD"):
            l = QLabel("")
            l.setFont(QFont("Courier New", 8))
            l.setStyleSheet(f"""
                QLabel {{ color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 12px;
                    padding: 3px 10px; background: transparent; }}""")
            self._vitals_row.addWidget(l)
            self._vital_lbls[key] = l

        # SHARE SCREEN toggle — one click, and Jarvis starts watching the
        # screen to coach. Styled like the vitals chips so it does not shout.
        self._share_btn = QPushButton("◈ SHARE")
        self._share_btn.setFont(QFont("Courier New", 8))
        self._share_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._share_btn.setToolTip(
            "Bagikan layar: Jarvis melihat dan membimbing (tidak mengklik)")
        self._share_btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: 1px solid {C.BORDER}; border-radius: 12px;
                padding: 3px 10px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; }}
        """)
        self._share_btn.clicked.connect(self._toggle_screen_share)
        self._vitals_row.addWidget(self._share_btn)

        self._vitals_row.addStretch()
        v.addLayout(self._vitals_row)

        # ── custom-brain progress ───────────────────────────────────────
        # One quiet line: what the brain is doing right now ("memahami…",
        # "menjalankan gmail_send…"). It keeps its height when empty, so no
        # other element moves, and it never covers the HUD.
        self._task_lbl = QLabel("")
        self._task_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._task_lbl.setFont(QFont("Courier New", 8))
        self._task_lbl.setFixedHeight(16)
        self._task_lbl.setStyleSheet(
            f"QLabel {{ color: {C.PRI}; background: transparent; }}")
        v.addWidget(self._task_lbl)

        # ── the pill ────────────────────────────────────────────────────
        bar = QFrame()
        bar.setFixedHeight(58)
        bar.setStyleSheet(f"""
            QFrame {{ background: rgba(3,12,28,0.82);
                border: 2px solid {C.PRI}; border-radius: 29px; }}""")
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(20, 0, 10, 0)
        bh.setSpacing(10)

        icon = QLabel("✦")
        icon.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
        icon.setStyleSheet(f"color: {C.ACC}; background: transparent; border: none;")
        bh.addWidget(icon)

        self._input_center = QLineEdit()
        self._input_center.setPlaceholderText(
            "Tanya JARVIS apa saja…   (Ctrl + K)")
        self._input_center.setFont(QFont("Courier New", 12))
        self._input_center.setFixedHeight(42)
        self._input_center.setStyleSheet(
            "QLineEdit { background: transparent; color: "
            f"{C.TEXT}; border: none; padding: 0 4px; }}")
        self._input_center.returnPressed.connect(self._send_center)
        try:
            from PyQt6.QtGui import QShortcut
            QShortcut("Ctrl+K", self._input_center).activated.connect(
                self._input_center.setFocus)
        except Exception:
            pass
        bh.addWidget(self._input_center, 1)

        def _pill_btn(text, go=False):
            b = QPushButton(text)
            b.setFixedHeight(38)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
            if go:
                b.setStyleSheet(f"""
                    QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 {C.PRI_DIM}, stop:1 {C.PRI}); color: #021020;
                        border: 0; border-radius: 19px; padding: 0 18px; }}
                    QPushButton:hover {{ background: {C.PRI}; }}""")
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER_B}; border-radius: 19px;
                        padding: 0 16px; }}
                    QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}""")
            return b

        self._voice_btn_center = _pill_btn("Suara")
        self._voice_btn_center.clicked.connect(self._qc_voice)
        bh.addWidget(self._voice_btn_center)
        send = _pill_btn("Kirim", go=True)
        send.clicked.connect(self._send_center)
        bh.addWidget(send)
        v.addWidget(bar)

        # ── quick chips ─────────────────────────────────────────────────
        chips = QHBoxLayout()
        chips.setSpacing(10)
        chips.addStretch()
        for text in ("Analisis proyek", "Cari berkas", "Buat otomasi",
                     "Buka terminal"):
            c = QPushButton(text)
            c.setCursor(Qt.CursorShape.PointingHandCursor)
            c.setFont(QFont("Courier New", 9))
            c.setStyleSheet(f"""
                QPushButton {{ background: rgba(63,216,255,0.06);
                    color: {C.TEXT_MED};
                    border: 1px solid {C.BORDER_B}; border-radius: 18px;
                    padding: 7px 16px; }}
                QPushButton:hover {{ color: {C.WHITE};
                    border-color: {C.PRI_DIM}; }}""")
            c.clicked.connect(
                lambda _=False, t=text: self._quick_chip(t))
            chips.addWidget(c)
        chips.addStretch()
        v.addLayout(chips)
        return wrap

    _QUICK_CHIP_CMDS = {
        "Analisis proyek": "Analisis proyek di folder kerjaku dan rangkum temuanmu",
        "Cari berkas": "Cari berkas bernama ",
        "Buat otomasi": "Buat otomasi: ",
        "Buka terminal": "Buka terminal",
    }

    def _quick_chip(self, text: str) -> None:
        """A chip pre-fills the pill; the user reads/edits, Enter sends."""
        self._input_center.setText(self._QUICK_CHIP_CMDS.get(text, text))
        self._input_center.setFocus()

    def _refresh_vitals(self, latency_ms: float | None = None,
                        toks: float | None = None,
                        context_pct: float | None = None) -> None:
        """Vital chips read only what the session actually measured.

        Called by the metric sampler. Numbers the session has not reported
        show as "—", never as invented placeholders. RAHASIA pill mirrors
        the Privacy Shield: on when redaction is on.
        """
        lbls = self._vital_lbls
        if not lbls:
            return
        lat = f"{latency_ms:.0f} ms" if latency_ms else "—"
        tok = f"{toks:.0f} tok/s" if toks else "—"
        ctx = f"KONTEKS {context_pct:.0f}%" if context_pct else "KONTEKS —"
        try:
            from memory.config_manager import get_privacy_shield
            shield = get_privacy_shield()["enabled"]
        except Exception:
            shield = False
        for key, text in (("LATENSI", f"LATENSI {lat}"), ("TOKS", tok),
                          ("KONTEKS", ctx),
                          ("SHIELD", "RAHASIA DISAMARKAN" if shield
                           else "MODE BIASA")):
            lbls[key].setText(text)

    def _send_center(self) -> None:
        """The big ask bar under the face — same path as the rail box."""
        self._send(source="center")

    # ── bottom cards: models, tools, recent work ────────────────────────────

    def _build_bottom_cards(self) -> QWidget:
        """The strip along the centre column's bottom — three cards.

        The second reference keeps this row INSIDE the centre column (between
        sidebar and right rail) and gives it three cards: RECENT PROJECTS,
        AI MODELS, TOOLS & AGENTS. Widths from the photo: the three split
        the centre column's 910 px roughly 30/33/34 with equal gutters.

        Every card reads something that already exists: core/recent.py, the
        provider config, and the plugin registry. Nothing here is a picture
        of a feature. The cards the layout dropped were not deleted — they
        still exist (SYSTEM HEALTH moved up to the centre stage, SAFETY and
        RECENT RUNS stay reachable from the rail's log) — they are simply no
        longer pinned to this strip.
        """
        row = QWidget()
        row.setStyleSheet(f"background: {C.DARK};")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(18, 8, 18, 10)
        lay.setSpacing(18)
        for card, stretch in (
            (self._card_workspace(), 30),   # OTOMASI TERJADWAL
            (self._card_models(),    30),   # AI MODELS
            (self._card_tools(),     30),   # TOOLS & AGENTS
            (self._card_approvals(), 28),   # ANTREAN PERSETUJUAN
        ):
            # 120 px is where a card stops being readable but still does not
            # clip: three of them plus two 18 px gutters is 396.
            card.setMinimumWidth(120)
            lay.addWidget(card, stretch=stretch)
        return row

    def _card_shell(self, title: str, btn_text: str = "", btn_cb=None,
                    badge: str = "", badge_color: str = C.GREEN):
        """One card's furniture: the frame, the title row, and — optionally —
        a badge on the right, a button on the right, or both.

        The reference uses a coloured word where we would use a button (the
        SYSTEM HEALTH card says Healthy, not Open), so the callers can ask
        for either without a second code path.
        """
        card = FuturisticFrame()
        # The frame paints its own chamfered body; the stylesheet only has to
        # keep child widgets transparent so the gradient shows through.
        card.setStyleSheet("FuturisticFrame { background: transparent; border: none; }")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 11, 14, 11)
        v.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(6)
        t = QLabel(f"▸ {title}")
        t.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        hdr.addWidget(t)
        hdr.addStretch()
        card._badge = None
        if badge:
            bl = QLabel(badge)
            bl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            bl.setStyleSheet(f"color: {badge_color}; background: transparent; border: none;")
            hdr.addWidget(bl)
            card._badge = bl
        if btn_text:
            b = QPushButton(btn_text)
            b.setFixedHeight(22)
            b.setFont(QFont("Courier New", 8))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.BORDER}; border-radius: 3px; padding: 0 6px; }}
                QPushButton:hover {{ border-color: {C.PRI_DIM}; background: {C.PRI_GHO}; }}""")
            if btn_cb is not None:
                b.clicked.connect(btn_cb)
            hdr.addWidget(b)
        v.addLayout(hdr)
        return card, v

    def _card_models(self) -> QWidget:
        card, v = self._card_shell("AI MODELS", "KELOLA MODEL", self._open_providers,
                                   badge="0 tersedia", badge_color=C.TEXT_DIM)
        self._models_badge = card._badge
        self._models_box = QVBoxLayout()
        self._models_box.setSpacing(2)
        v.addLayout(self._models_box)
        v.addStretch(1)
        self._refresh_models_card()
        return card

    def _refresh_models_card(self) -> None:
        """Every model the user has entered, the one the heavy-work brain is
        pointed at marked — the same config the board edits, read here so the
        strip can never disagree with it."""
        if not hasattr(self, "_models_box"):
            return
        while self._models_box.count():
            it = self._models_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()          # stop painting this frame; deleteLater frees later
                w.deleteLater()
        try:
            from memory.config_manager import (get_active_provider,
                                               get_provider_config,
                                               list_provider_ids)
            ids = list_provider_ids()
            active = get_active_provider() or (ids[0] if ids else "")
        except Exception:
            ids, active = [], ""

        total = 0
        rows: list[tuple[str, str, bool, str]] = []
        for pid in ids:
            cfg = get_provider_config(pid)
            models = [m for m in (cfg.get("models") or []) if str(m).strip()]
            if not models and cfg.get("model"):
                models = [cfg["model"]]
            total += len(models)
            for i, m in enumerate(models):
                is_active = (pid == active and str(m) == str(cfg.get("model")))
                # The provider id is printed once, against its first model.
                rows.append((str(m), pid if i == 0 else "", is_active,
                             f"{pid} · {cfg.get('base_url', '')}"
                             f" · tier {', '.join(cfg.get('tiers') or []) or '—'}"))

        if self._models_badge is not None:
            self._models_badge.setText(f"{total} tersedia")
            self._models_badge.setStyleSheet(
                f"color: {C.PRI if total else C.TEXT_DIM};"
                f" background: transparent; border: none;")

        if not rows:
            lbl = QLabel("Belum ada model custom")
            lbl.setFont(QFont("Courier New", 8))
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            self._models_box.addWidget(lbl)
            return

        for name, pid, is_active, tip in rows[:5]:
            txt = f"{'●' if is_active else '○'}  {name}"
            if pid:
                txt += f"   ·   {pid}"
            row = QLabel(txt)
            row.setFont(QFont("Courier New", 9,
                              QFont.Weight.Bold if is_active else QFont.Weight.Normal))
            row.setStyleSheet(
                f"color: {C.PRI if is_active else C.TEXT_MED};"
                f" background: transparent; border: none;")
            row.setToolTip(tip)
            self._models_box.addWidget(row)
        if len(rows) > 5:
            more = QLabel(f"+{len(rows) - 5} model lagi")
            more.setFont(QFont("Courier New", 7))
            more.setStyleSheet(
                f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            self._models_box.addWidget(more)

    def _card_tools(self) -> QWidget:
        card, v = self._card_shell("TOOLS & AGENTS", "LIHAT", self._nav_tools)
        self._tools_box = QVBoxLayout()
        self._tools_box.setSpacing(2)
        v.addLayout(self._tools_box)
        v.addStretch(1)
        self._refresh_tools_card()
        return card

    def _refresh_tools_card(self) -> None:
        if not hasattr(self, "_tools_box"):
            return
        while self._tools_box.count():
            it = self._tools_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()          # stop painting this frame; deleteLater frees later
                w.deleteLater()
        try:
            plugins = (self.get_plugins() if self.get_plugins else []) or []
        except Exception:
            plugins = []
        # Real MCP tool count, summed over live servers — the same number the
        # Connections board shows. This is what the mockup's "MCP Tools 207"
        # card becomes: the true figure instead of the mockup's example.
        mcp_tools = 0
        mcp_alive = 0
        mcp_total = 0
        try:
            servers = (self.get_mcp_servers() if self.get_mcp_servers else []) or []
        except Exception:
            servers = []
        for srv in servers:
            mcp_total += 1
            if srv.get("alive"):
                mcp_alive += 1
                mcp_tools += int(srv.get("tools") or 0)

        rows = []
        if mcp_total:
            col = C.GREEN if mcp_alive else C.TEXT_DIM
            rows.append((f"MCP Tools   {mcp_tools}", col,
                         f"{mcp_alive}/{mcp_total} server hidup"))
        for p in plugins[:3]:
            rows.append((f"●  {str(p.get('name', '?'))[:22]}",
                         C.GREEN, "plugin"))
        if not rows:
            lbl = QLabel("Daftar plugin belum dimuat")
            lbl.setFont(QFont("Courier New", 8))
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            self._tools_box.addWidget(lbl)
            return
        for txt, col, tip in rows[:4]:
            row = QLabel(txt)
            row.setFont(QFont("Courier New", 9))
            row.setStyleSheet(f"color: {col}; background: transparent; border: none;")
            row.setToolTip(tip)
            self._tools_box.addWidget(row)
        if len(rows) > 4:
            more = QLabel(f"+{len(rows) - 4} lagi")
            more.setFont(QFont("Courier New", 7))
            more.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            self._tools_box.addWidget(more)

    # ── card 4: ANTREAN PERSETUJUAN ────────────────────────────────────────

    def _card_approvals(self) -> QWidget:
        """The mockup's approval-queue card, made real: actions JARVIS
        considers risky park here until the user presses Setujui.

        Producers call request_approval(title, reason, on_yes) — anything
        with a callback. With nothing pending the card says so and gets out
        of the way; nothing here is a canned example.
        """
        card, v = self._card_shell("ANTREAN PERSETUJUAN", "",
                                   badge="0", badge_color=C.TEXT_DIM)
        card.setStyleSheet(
            f"QFrame {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 {C.PANEL2}, stop:1 {C.DARK});"
            f" border: 1px solid {C.ACC}; border-radius: 8px; }}")
        self._approvals_badge = card._badge
        self._approvals_box = QVBoxLayout()
        self._approvals_box.setSpacing(4)
        v.addLayout(self._approvals_box)
        self._approvals_empty = QLabel(
            "Tidak ada aksi yang menunggu izin")
        self._approvals_empty.setFont(QFont("Courier New", 8))
        self._approvals_empty.setWordWrap(True)
        self._approvals_empty.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        self._approvals_box.addWidget(self._approvals_empty)
        self._approvals: list[dict] = []
        return card

    def request_approval(self, title: str, reason: str, on_yes) -> int:
        """Queue one action for the user's yes/no. Returns the ticket id.

        `on_yes` runs on the UI thread when Setujui is pressed; Tolak just
        drops the ticket. Centre-styled like the mockup: amber frame card
        with a Setujui (filled) and a Tolak (outline) button per ticket.
        """
        self._approvals.append({
            "id": time.time(), "title": str(title)[:60],
            "reason": str(reason)[:80], "cb": on_yes,
        })
        self._refresh_approvals()
        return len(self._approvals)

    def _refresh_approvals(self) -> None:
        while self._approvals_box.count() > 1:      # keep the empty-label
            it = self._approvals_box.takeAt(1)
            w = it.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        n = len(self._approvals)
        if self._approvals_badge is not None:
            self._approvals_badge.setText(str(n))
            self._approvals_badge.setStyleSheet(
                f"color: {C.ACC if n else C.TEXT_DIM};"
                f" background: transparent; border: none;")
        self._approvals_empty.setVisible(n == 0)
        for ticket in self._approvals[:3]:
            row = QFrame()
            row.setStyleSheet(
                f"QFrame {{ background: {C.PRI_GHO if False else 'transparent'};"
                f" border: 1px solid {C.BORDER_A}; border-radius: 6px; }}")
            rv = QVBoxLayout(row)
            rv.setContentsMargins(8, 6, 8, 6)
            rv.setSpacing(3)
            t = QLabel(ticket["title"])
            t.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            t.setWordWrap(True)
            t.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
            rv.addWidget(t)
            if ticket["reason"]:
                rsn = QLabel(ticket["reason"])
                rsn.setFont(QFont("Courier New", 7))
                rsn.setWordWrap(True)
                rsn.setStyleSheet(
                    f"color: {C.TEXT_DIM}; background: transparent; border: none;")
                rv.addWidget(rsn)
            btns = QHBoxLayout()
            btns.setSpacing(6)
            yes = QPushButton("Setujui")
            yes.setFixedHeight(24)
            yes.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            yes.setCursor(Qt.CursorShape.PointingHandCursor)
            yes.setStyleSheet(f"""
                QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {C.PRI_DIM}, stop:1 {C.PRI}); color: #021020;
                    border: 0; border-radius: 12px; padding: 0 10px; }}
                QPushButton:hover {{ background: {C.PRI}; }}""")
            no = QPushButton("Tolak")
            no.setFixedHeight(24)
            no.setFont(QFont("Courier New", 8))
            no.setCursor(Qt.CursorShape.PointingHandCursor)
            no.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.TEXT_MED};
                    border: 1px solid {C.BORDER}; border-radius: 12px;
                    padding: 0 10px; }}
                QPushButton:hover {{ border-color: {C.RED}; color: {C.RED}; }}""")
            cb = ticket["cb"]
            yes.clicked.connect(lambda _=False, tk=ticket: self._approve(tk, cb))
            no.clicked.connect(lambda _=False, tk=ticket: self._reject(tk))
            btns.addWidget(yes)
            btns.addWidget(no)
            btns.addStretch()
            rv.addLayout(btns)
            self._approvals_box.addWidget(row)

    def _approve(self, ticket: dict, cb) -> None:
        self._approvals = [t for t in self._approvals if t is not ticket]
        self._refresh_approvals()
        self._log.append_log(f"SYS: Disetujui — {ticket['title']}")
        if cb:
            try:
                cb()
            except Exception as e:              # noqa: BLE001
                self._log.append_log(f"ERR: approval action: {e}")

    def _reject(self, ticket: dict) -> None:
        self._approvals = [t for t in self._approvals if t is not ticket]
        self._refresh_approvals()
        self._log.append_log(f"SYS: Ditolak — {ticket['title']}")

    # ── card 3: WORKSPACE ───────────────────────────────────────────────────

    def _card_workspace(self) -> QWidget:
        """Scheduled work — the mockup's 'Otomasi Terjadwal' card, fed by
        what the app really has: the morning brief (with its real on/off
        state and time) and the user's saved macros. A card of fake alarms
        would have been worse than no card, so everything listed here can
        actually fire.
        """
        card, v = self._card_shell("OTOMASI TERJADWAL", "KELOLA",
                                   self._open_providers,
                                   badge="", badge_color=C.TEXT_DIM)
        self._ws_badge = card._badge
        self._ws_box = QVBoxLayout()
        self._ws_box.setSpacing(2)
        v.addLayout(self._ws_box)
        v.addStretch(1)
        self._ws_note = QLabel("")
        self._ws_note.setFont(QFont("Courier New", 8))
        self._ws_note.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        v.addWidget(self._ws_note)
        self._refresh_workspace_card()
        return card

    def _open_newest_recent(self) -> None:
        """The card's button: show the newest path, or say there is none."""
        try:
            from core import recent
            items = recent.items(1)
        except Exception:
            items = []
        if items:
            self._show_content("WORKSPACE", items[0]["path"])
        else:
            self._show_content("WORKSPACE",
                               "Belum ada berkas yang pernah dikerjakan JARVIS.")

    def _refresh_workspace_card(self) -> None:
        """Fill the scheduled-automation card from real config: the morning
        brief's actual enabled state + any saved macros. Re-run by the
        metric sampler, so toggling the brief flips the card within 2s."""
        if not hasattr(self, "_ws_box"):
            return
        while self._ws_box.count():
            it = self._ws_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        rows: list[tuple[str, str, str]] = []      # (text, colour, tooltip)
        try:
            from memory.config_manager import get_brief_enabled
            brief_on = bool(get_brief_enabled())
        except Exception:
            brief_on = False
        rows.append((
            ("●  Briefing pagi" if brief_on else "○  Briefing pagi"),
            C.GREEN if brief_on else C.TEXT_DIM,
            "Ringkasan pagi otomatis — aktif di ⚙ (Setup)"))
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "memory", "macros.json"), encoding="utf-8") as f:
                macros = (json.load(f).get("macros") or {})
        except Exception:
            macros = {}
        for name in list(macros)[:4]:
            rows.append((f"◆  {str(name)[:26]}", C.PRI, "Macro tersimpan"))
        if self._ws_badge is not None:
            n = len(rows)
            self._ws_badge.setText(f"{n} terdaftar")
            self._ws_badge.setStyleSheet(
                f"color: {C.PRI if n else C.TEXT_DIM};"
                f" background: transparent; border: none;")
        if not rows[1:]:
            note = QLabel("Belum ada macro — aktivasi lewat perintah suara")
            note.setFont(QFont("Courier New", 7))
            note.setStyleSheet(
                f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            self._ws_box.addWidget(note)
        for txt, col, tip in rows[:5]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Courier New", 9))
            lbl.setStyleSheet(
                f"color: {col}; background: transparent; border: none;")
            lbl.setToolTip(tip)
            self._ws_box.addWidget(lbl)

    def _show_activity_log(self) -> None:
        """The rail's "Lihat semua" action: the log is always visible now, so
        this opens the full transcript in the content panel."""
        try:
            rows = getattr(self, "_runs", [])
            body = "\n".join(f"{t}  {m}" for t, m in rows[-40:]) or "(log kosong)"
            self._show_content("ACTIVITY LOG", body)
        except Exception:
            pass

    def _record_run(self, text: str) -> None:
        """One ring buffer, fed by the same signal that feeds the log."""
        try:
            if not hasattr(self, "_runs"):
                self._runs: list[tuple[str, str]] = []
            self._runs.append((time.strftime("%H:%M"), str(text)))
            if len(self._runs) > 40:
                del self._runs[:len(self._runs) - 40]
            self._runs_dirty = True
        except Exception:
            pass
        self.observe_task(str(text))

    def observe_task(self, text: str) -> None:
        """Feed the Live Task Graph from the real session traffic.

        `write_log` carries everything the session says; the tool-shaped
        lines are recognised here and turned into graph steps. Patterns come
        from what the session actually emits (tool execution notices, SYS
        activity) — anything unrecognised is ignored, so the graph stays a
        picture of real work rather than becoming a second log.
        """
        tg = getattr(self, "_task_graph", None)
        if tg is None:
            return
        t = text.strip()
        low = t.lower()
        try:
            if low.startswith(("tool:", "exec:", "run:", "▸", "⚡")) \
                    or " tool " in low or low.startswith("mcp:"):
                # "tool done" notices carry durations when the session knows
                # them ("files.search · 212 ms"); pass the number through.
                ms = None
                if " ms" in low:
                    try:
                        ms = float(t.split("·")[-1].strip().split()[0])
                    except Exception:
                        ms = None
                tg.mark(t[:38], "ok", ms)
            elif " berjalan" in low or low.startswith(("mencari", "membuka",
                                                       "menjalankan")):
                tg.mark(t[:38], "run")
            elif low.startswith(("sys:", "err:")):
                return          # bookkeeping, not task steps
        except Exception:
            pass

    # ── SAFETY rows now live in the status verdict + footer; the card is
    # gone, but the refresh entry points remain no-ops so old callers keep
    # working. ──────────────────────────────────────────────────────────────

    def _refresh_safety_card(self) -> None:
        pass

    # ── quick controls (right column) ───────────────────────────────────────

    def _build_quick_controls(self) -> QGridLayout:
        """Four tiles, four existing behaviours — no new code paths.

        Each tile is the reference's two-line shape: the action on top, a
        live status word under it. The status words are read from the same
        state the rest of the UI reads (wake gate, mute flag), so they are
        not decoration.
        """
        g = QGridLayout()
        g.setSpacing(6)

        # Each cell = one framed tile: action on top, live status under it.
        tiles = [
            ("VOICE",     self._qc_voice,      "Aktif / nonaktifkan wake word"),
            ("CAPTURE",   self._qc_screenshot, "Ambil screenshot lewat JARVIS"),
            ("RESTART",   self._qc_restart,    "Bangun ulang sesi percakapan"),
            ("SYS CHECK", self._qc_system,     "Minta JARVIS melaporkan kesehatan sistem"),
        ]
        self._qc_subs: list[QLabel] = []
        for i, (title, cb, tip) in enumerate(tiles):
            r, c = divmod(i, 2)
            box = QFrame()
            box.setStyleSheet(f"QFrame {{ background: {C.PANEL2};"
                              f" border: 1px solid {C.BORDER}; border-radius: 5px; }}")
            bv = QVBoxLayout(box)
            bv.setContentsMargins(8, 6, 8, 5)
            bv.setSpacing(1)
            b = QPushButton(title)
            b.setFixedHeight(20)
            b.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.TEXT};
                    border: none; text-align: left; padding: 0; }}
                QPushButton:hover {{ color: {C.PRI}; }}""")
            b.clicked.connect(cb)
            bv.addWidget(b)
            sub = QLabel("●  —")
            sub.setFont(QFont("Courier New", 7))
            sub.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
            bv.addWidget(sub)
            self._qc_subs.append(sub)
            g.addWidget(box, r, c)

        return g

    def _refresh_qc_subs(self) -> None:
        """Live sub-labels: the two states a tile can actually see."""
        subs = getattr(self, "_qc_subs", None)
        if not subs:
            return
        try:
            st = (self.wake_get_state() or {}) if self.wake_get_state else {}
            voice = "Listening" if st.get("enabled") else "Off"
        except Exception:
            voice = "—"
        cap = "Ready"
        try:
            rest = "Idle"
        except Exception:
            rest = "—"
        health = {"SPEAKING": "Busy", "THINKING": "Working"}.get(
            str(getattr(self, "_last_state", "")).upper(), "Healthy")
        words = [voice, cap, rest, health]
        for sub, word in zip(subs, words):
            sub.setText(f"●  {word}")

    def _qc_voice(self) -> None:
        try:
            st = (self.wake_get_state() or {}) if self.wake_get_state else {}
            enabled = bool(st.get("enabled"))
            msg = ""
            if self.on_wake_toggle:
                msg = self.on_wake_toggle(not enabled) or ""
            self._log.append_log("SYS: " + (msg or f"Wake word → {'OFF' if enabled else 'ON'}"))
        except Exception as e:                  # noqa: BLE001
            self._log.append_log(f"ERR: voice mode: {e}")

    def _qc_screenshot(self) -> None:
        if self.on_text_command:
            self.on_text_command(
                "Ambil screenshot layar sekarang, simpan ke folder yang wajar, "
                "lalu konfirmasi singkat.")
        else:
            self._log.append_log("ERR: sesi belum siap untuk perintah.")

    def _qc_restart(self) -> None:
        try:
            if self.on_voice_change:
                self.on_voice_change()
                self._log.append_log("SYS: Sesi dibangun ulang.")
            else:
                self._log.append_log("ERR: restart belum tersedia.")
        except Exception as e:                  # noqa: BLE001
            self._log.append_log(f"ERR: restart: {e}")

    def _qc_system(self) -> None:
        if self.on_text_command:
            self.on_text_command(
                "Cek status sistem: CPU, RAM, disk, dan GPU. Laporkan singkat "
                "dalam satu dua kalimat.")
        else:
            self._log.append_log("ERR: sesi belum siap untuk perintah.")

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        self._BTN_PRI, self._BTN_DIM = _BTN_STYLE_PRI, _BTN_STYLE_DIM
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ SETUP")
        hdr.setFont(_ui_font(10, bold=True))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(36)
        remote_btn.setFont(_ui_font(11, bold=True))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(32)
        sc_btn.setFont(_ui_font(10))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(32)
        self._autostart_btn.setFont(_ui_font(10))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(32)
        cust_btn.setFont(_ui_font(10))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(32)
        audio_btn.setFont(_ui_font(10))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(32)
        mem_btn.setFont(_ui_font(10))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(32)
        plugin_btn.setFont(_ui_font(10))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        conn_btn = QPushButton("🔌  CONNECTIONS")
        conn_btn.setFixedHeight(32)
        conn_btn.setFont(_ui_font(10))
        conn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        conn_btn.setStyleSheet(_BTN_STYLE_DIM)
        conn_btn.clicked.connect(self._open_connections)
        lay.addWidget(conn_btn)

        provider_btn = QPushButton("🔌  AI PROVIDER")
        provider_btn.setFixedHeight(32)
        provider_btn.setFont(_ui_font(10))
        provider_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        provider_btn.setStyleSheet(_BTN_STYLE_DIM)
        provider_btn.clicked.connect(self._open_providers)
        lay.addWidget(provider_btn)

        w.adjustSize()
        return w

    def _build_controls_drawer(self) -> QWidget:
        """The switches that get flicked every day, in their own panel."""
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(self._quick_drawer.styleSheet())
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(_ui_font(10, bold=True))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        def _row(button, style=None, height=32):
            button.setFixedHeight(height)
            button.setFont(_ui_font(10))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if style:
                button.setStyleSheet(style)
            lay.addWidget(button)
            return button

        fs_btn = _row(QPushButton("⛶  FULLSCREEN  [F11]"), self._BTN_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)

        self._brief_btn = _row(QPushButton())
        self._brief_btn.clicked.connect(self._toggle_brief)

        self._wake_btn = _row(QPushButton())
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        self._wake_sleep_btn = _row(QPushButton())
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        # Neutral until the real state lands from the background warm-up.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(self._BTN_DIM)
        self._wake_sleep_btn.hide()

        self._ptt_btn = _row(QPushButton())
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        self._refresh_talk_btns()

        self._hud_btn = _row(QPushButton())
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        self._refresh_hud_btn()

        self._anim_btn = _row(QPushButton())
        self._anim_btn.clicked.connect(self._toggle_animations)
        self._refresh_anim_btn()

        w.adjustSize()
        return w

    def _warm_wake_state(self) -> None:
        """Work out the wake-word state off the UI thread, once.

        It used to be resolved when the drawer opened, and that made opening the
        drawer take two seconds the first time — measured at 2.10s, all of it
        `import openwakeword` dragging in onnxruntime behind it. The button
        count had nothing to do with it; the Qt thread was simply waiting on an
        import. Afterwards the same check costs about a millisecond, so it only
        ever needed to happen somewhere other than in front of the user.
        """
        def work():
            try:
                self._wake_state()
            except Exception:
                pass
            self._wake_btns_sig.emit()
        threading.Thread(target=work, daemon=True, name="wake-state-warm").start()

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._close_controls()
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _toggle_controls(self, checked: bool):
        if checked:
            self._close_setup()
            self._refresh_wake_btns()   # cheap now: the state was warmed at boot
            self._position_ctrl_drawer()
            self._ctrl_drawer.show()
            self._ctrl_drawer.raise_()
        else:
            self._ctrl_drawer.hide()

    def _close_setup(self):
        if hasattr(self, "_quick_drawer"):
            self._quick_drawer.hide()
        if hasattr(self, "_drawer_btn"):
            self._drawer_btn.setChecked(False)

    def _close_controls(self):
        if hasattr(self, "_ctrl_drawer"):
            self._ctrl_drawer.hide()
        if hasattr(self, "_ctrl_btn"):
            self._ctrl_btn.setChecked(False)

    # One width for both drawers — set here so the centring math below can't
    # drift from what _place_drawer enforces. (Reading `drawer.width()` during
    # placement returned a stale 376px pre-layout value, which shoved the
    # "centred" drawer 78px left of true centre at 1536px wide.)
    _DRAWER_W = 220

    def _place_drawer(self, drawer, left: int):
        _W = self._DRAWER_W
        drawer.setFixedWidth(_W)
        drawer.adjustSize()
        drawer.setGeometry(left, 54, _W, drawer.sizeHint().height())

    def _position_quick_drawer(self):
        # Centered in the window — anchoring a 220px panel to a corner button
        # let it clip against the window edge; centred it always fits.
        if hasattr(self, "_quick_drawer"):
            self._place_drawer(self._quick_drawer, self._drawer_center_x())

    def _drawer_center_x(self) -> int:
        """X that centres a drawer over the window, edge-clamped.

        Uses the declared `_DRAWER_W`, not `drawer.width()` — that reports a
        stale 376px before the layout pass, which landed the "centred" drawer
        78px left of true centre at a 1536px window.
        """
        try:
            raw = (self.window().width() - self._DRAWER_W) // 2
        except Exception:
            raw = 12
        return max(12, raw)

    def _position_ctrl_drawer(self):
        # Centred too, for the same reason — and so both drawers read as one
        # pattern: open straight down the middle of the dashboard.
        if hasattr(self, "_ctrl_drawer"):
            self._place_drawer(self._ctrl_drawer, self._drawer_center_x())

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Courier New", 11))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Courier New", 11))
        dismiss.setFixedHeight(24)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Courier New", 13))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: JARVIS says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message JARVIS reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont("Courier New", 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 2px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont("Courier New", 7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont("Courier New", 7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont("Courier New", 9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont("Courier New", 8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont("Courier New", 9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are JARVIS's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to JARVIS as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    # Ukuran huruf strip bawah dicari, bukan dipaku: `_fit_footer` mencoba
    # dari 14 pt ke bawah dan berhenti pada ukuran terbesar yang MUAT. Dulu
    # dipaku 7–8 pt, jadi di layar lebar teksnya tampak seperti bintik
    # (keluhan bos 25 Okt: "perbesar font di bagian bawah ... pojok kiri kanan").
    _FOOTER_PT_MAX = 14
    _FOOTER_PT_MIN = 7

    def _build_footer(self) -> QWidget:
        """The strip under everything — the reference has one and it carries
        two facts, one at each end: what this run is, and what the voice is
        doing. Both are read from the app rather than written into the file.

        Ukuran huruf mengikuti lebar strip (lihat `_fit_footer`).
        """
        w = _FooterStrip(self._fit_footer)
        w.setFixedHeight(32)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(6)

        def _fl(txt, color=C.TEXT_MED, size=7):
            l = QLabel(txt); l.setFont(QFont("Courier New", size))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        # "No cloud transmission" would be a lie here — prompts do go to the
        # model the user configured. What is true, and what the reference's
        # badge is really about, is where the record of the work is kept.
        _dot = QLabel("●")
        _dot.setFont(QFont("Courier New", 9))
        _dot.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        self._footer_dot = _dot
        lay.addWidget(_dot)
        self._footer_title = _fl("LOCAL MEMORY", C.GREEN, 8)
        lay.addWidget(self._footer_title)
        self._footer_sep = _fl("·")
        lay.addWidget(self._footer_sep)
        self._footer_caption = _ElideLabel(
            "riwayat & profil tersimpan di perangkat ini")
        self._footer_caption.setFont(QFont("Courier New", 7))
        self._footer_caption.setStyleSheet(
            f"color: {C.PRI_DIM}; background: transparent;")
        self._footer_caption_full = self._footer_caption.fullText()
        lay.addWidget(self._footer_caption)

        lay.addStretch()
        self._footer_hint = _fl("[F4] Mute  ·  [F11] Fullscreen")
        lay.addWidget(self._footer_hint)
        lay.addStretch()

        self._voice_btn = QPushButton("🎙  Voice listening: —  ⌄")
        self._voice_btn.setFixedHeight(20)
        self._voice_btn.setFont(QFont("Courier New", 8))
        self._voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._voice_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: none; padding: 0 4px; }}
            QPushButton:hover {{ color: {C.PRI}; }}""")
        self._voice_btn.setToolTip("Klik untuk membisukan / mengaktifkan mikrofon")
        self._voice_btn.clicked.connect(self._toggle_mute)
        lay.addWidget(self._voice_btn)
        self._footer_brand = _fl("By ARIS", C.PRI_DIM)
        lay.addWidget(self._footer_brand)

        self._footer_w = w
        self._footer_lay = lay
        self._footer_pt = None
        return w

    def _footer_need(self) -> int:
        """Lebar yang benar-benar dibutuhkan isi strip saat ini."""
        lay = getattr(self, "_footer_lay", None)
        if lay is None:
            return 0
        m = lay.contentsMargins()
        anak = [lay.itemAt(i).widget() for i in range(lay.count())]
        anak = [a for a in anak if a is not None and not a.isHidden()]
        total = sum(max(a.sizeHint().width(), a.minimumWidth()) for a in anak)
        total += lay.spacing() * max(0, len(anak) - 1)
        return total + m.left() + m.right()

    def _apply_footer_pt(self, pt: int) -> None:
        """Pasang satu ukuran huruf ke seluruh isi strip (tanpa menyentuh tinggi)."""
        if getattr(self, "_footer_pt", None) == pt:
            return
        self._footer_pt = pt
        for lb, naik in ((self._footer_dot, 2), (self._footer_title, 1),
                         (self._footer_sep, 0), (self._footer_caption, 0),
                         (self._footer_hint, 0), (self._footer_brand, 0)):
            lb.setFont(QFont("Courier New", pt + naik))
        self._voice_btn.setFont(QFont("Courier New", pt + 1))
        # Tombol suara berganti kata (On / Berbicara / Berpikir / Siaga / Off).
        # Beri lebar untuk kata TERPANJANG sekarang, supaya strip tidak
        # berubah lebar setiap kali JARVIS mulai bicara.
        self._voice_btn.setMinimumWidth(
            self._voice_btn.fontMetrics().horizontalAdvance(
                "🎙  Voice listening: Berbicara  ⌄") + 10)

    def _best_footer_pt(self, avail: int, hint: bool) -> int:
        """pt terbesar yang muat, untuk satu keadaan (petunjuk tampil/sembunyi)."""
        self._footer_hint.setVisible(hint)
        self._footer_caption.setText(self._footer_caption_full)
        for calon in range(self._FOOTER_PT_MAX, self._FOOTER_PT_MIN - 1, -1):
            self._apply_footer_pt(calon)
            if self._footer_need() <= avail:
                return calon
        return self._FOOTER_PT_MIN

    def _fit_footer(self, avail: int) -> None:
        """Besarkan huruf strip bawah sebesar yang masih muat di lebarnya.

        Urutannya:
          1. coba dua keadaan — dengan dan tanpa petunjuk [F4]/[F11] — lalu
             ambil ukuran huruf terbesar. Petunjuk hanya dipertahankan kalau
             ia tidak memakan ukuran huruf (di jendela lebar ia tetap tampil);
          2. tinggi strip & tombol suara ikut naik supaya tidak sesak;
          3. kalau 7 pt pun tak muat (jendela sangat sempit): petunjuk
             disembunyikan, lalu kalimat penjelas dipendekkan dengan elipsis —
             tidak ada teks yang terpotong separuh.

        Dipanggil dari resizeEvent strip itu sendiri, jadi `avail` selalu
        lebar yang benar. Penjaga `_fitting` mencegah rekursi, karena
        mengubah tinggi strip memicu layout ulang.
        """
        if getattr(self, "_footer_w", None) is None or avail <= 0:
            return
        if getattr(self, "_fitting", False):
            return
        self._fitting = True
        try:
            pt_dengan = self._best_footer_pt(avail, True)
            pt_tanpa = self._best_footer_pt(avail, False)
            if pt_tanpa > pt_dengan:
                pt, hint_on = pt_tanpa, False
            else:
                pt, hint_on = pt_dengan, True
            self._footer_hint.setVisible(hint_on)
            self._apply_footer_pt(pt)

            self._footer_w.setFixedHeight(16 + pt * 2)
            self._voice_btn.setFixedHeight(pt * 2 + 6)

            # Masih sesak? Petunjuk singkatan dulu yang pergi. Kalimat penjelas
            # tidak perlu dipotong di sini: `_ElideLabel` memendekkan dirinya
            # sendiri sesuai ruang yang benar-benar diberikan layout.
            if self._footer_need() > avail:
                self._footer_hint.setVisible(False)
        finally:
            self._fitting = False

    def _refresh_footer_voice(self) -> None:
        """One word for the mouth, from the two things that decide it."""
        btn = getattr(self, "_voice_btn", None)
        if btn is None:
            return
        if getattr(self, "_muted", False):
            word, col = "Off", C.TEXT_DIM
        else:
            state = str(getattr(self, "_last_state", "LISTENING") or "").upper()
            if state == "SPEAKING":
                word, col = "Berbicara", C.GREEN
            elif state == "THINKING":
                word, col = "Berpikir", C.ACC
            elif state in ("SLEEPING", "MUTED"):
                word, col = "Siaga", C.TEXT_DIM
            else:
                word, col = "On", C.GREEN
        if btn.text() != f"🎙  Voice listening: {word}  ⌄":
            btn.setText(f"🎙  Voice listening: {word}  ⌄")
            btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {col};
                    border: none; padding: 0 4px; }}
                QPushButton:hover {{ color: {C.PRI}; }}""")

    def _on_file_selected(self, path: str):
        # The RECENT PROJECTS card reads this list, so picking a file in the
        # HUD is the first thing it ever sees.
        try:
            from core import recent
            recent.add(path)
            self._refresh_workspace_card()
        except Exception:
            pass
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "JARVIS_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.jarvis.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "jarvis.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JARVIS_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.jarvis.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before JarvisLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt
                              else "🎚  PUSH-TO-TALK: OFF")
        self._ptt_btn.setStyleSheet(_on if ptt else _off)
        self._ptt_btn.setToolTip(
            "Microphone stays closed until you hold the key — nothing is sent "
            "while you are not holding it." if ptt
            else "Hold a key to talk instead of streaming the mic continuously.")


    def _refresh_hud_btn(self):
        from memory.config_manager import get_hud_style
        style_now = get_hud_style()
        # Neither state is "off", so all read as active — this is a choice
        # between four centrepieces, not a switch with a disabled side.
        _LABELS = {"face": "🧑  HUD: ANIMATED FACE",
                   "face25d": "💀  HUD: MASK 2.5D",
                   "core": "◉  HUD: REACTOR CORE",
                   "orbit": "🛰  HUD: ORBIT WIREFRAME"}
        _TIPS = {
            "face": "An animated head that speaks your words and shows what "
                    "JARVIS is doing. Tap to cycle to the 2.5D mask.",
            "face25d": "The 2.5D mask: a red armoured face with orbit rings, "
                       "waveform and embers that react to your voice. Tap to "
                       "cycle to the reactor core.",
            "core": "A reactor core that turns with the state and moves with "
                    "your voice. Tap to cycle to the orbit wireframe.",
            "orbit": "The design-mockup centrepiece: a wireframe head inside "
                     "spinning orbit rings. Tap to cycle to the animated face.",
        }
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        self._hud_btn.setText(_LABELS.get(style_now, _LABELS["face"]))
        self._hud_btn.setStyleSheet(style)
        self._hud_btn.setToolTip(_TIPS.get(style_now, _TIPS["face"]))

    def _toggle_hud_style(self):
        """Cycle the centrepiece: face → face25d → core → orbit → face. All
        four stay in memory, so the change is instant and switching back costs
        nothing. Setiap gaya baru di _NEXT WAJIB juga masuk HUD_STYLES di
        memory/config_manager.py — kalau tidak, save_hud_style() diam-diam
        menulis "face" dan tombol ini berhenti berputar."""
        from memory.config_manager import get_hud_style, save_hud_style
        _NEXT = {"face": "face25d", "face25d": "core",
                 "core": "orbit", "orbit": "face"}
        want = _NEXT.get(get_hud_style(), "face")
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        _SAYS = {"face": "the animated face.",
                 "face25d": "the 2.5D mask.",
                 "core": "the reactor core.",
                 "orbit": "the orbit wireframe."}
        self._log.append_log(f"SYS: HUD switched to {_SAYS.get(want)}")

    def _refresh_anim_btn(self):
        on = getattr(getattr(self, "hud", None), "animations", True)
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        self._anim_btn.setText("🎬  ANIMASI: ON" if on else "🎬  ANIMASI: OFF")
        self._anim_btn.setStyleSheet(style)
        self._anim_btn.setToolTip(
            "Matikan semua animasi HUD (cincin orbit, reaktor, scanline). "
            "UI tetap berfungsi penuh; konsumsi CPU turun.")

    def _toggle_animations(self):
        from memory.config_manager import save_animations_enabled
        new = not getattr(self.hud, "animations", True)
        self.hud.animations = new
        save_animations_enabled(new)
        if new:
            self.hud._tmr.start(16)      # resume the stepping loop
        else:
            self.hud._tmr.stop()         # kill the loop now, not next tick
            self.hud.update()            # draw the frozen final frame
        self._refresh_anim_btn()
        self._log.append_log(
            "SYS: Animasi HUD dimatikan." if not new
            else "SYS: Animasi HUD dinyalakan.")

    def _set_hud_style(self, want: str) -> None:
        """Jump straight to a centrepiece (the 🎛 Controls HUD button)."""
        from memory.config_manager import save_hud_style
        if want not in ("face", "face25d", "core", "orbit"):
            return
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        _SAYS = {"face": "the animated face.", "face25d": "the 2.5D mask.",
                 "core": "the reactor core.", "orbit": "the orbit wireframe."}
        self._log.append_log(f"SYS: HUD switched to {_SAYS.get(want, want)}")

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through JarvisLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "JARVIS") or "JARVIS",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "JARVIS"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        self._title_lbl.setText(display)
        if display in ("JARVIS", "J.A.R.V.I.S"):
            self._sub_lbl.setText("Just A Rather Very Intelligent System")
        else:
            self._sub_lbl.setText("Personal AI Assistant")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            # Audit-3 25 Okt: lewat jalur atomik, bukan write_text langsung.
            from memory.config_manager import save_config as _save_cfg
            _save_cfg(data)
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── AI provider settings ─────────────────────────────────────────────────

    def _open_providers(self):
        """The model board: every custom provider, one active choice."""
        ov = ProvidersOverlay(parent=self.centralWidget())
        ov.changed.connect(self._on_providers_changed)
        self._centre_overlay(ov)
        self._providers_overlay = ov           # keep a reference so it isn't GC'd

    def _on_providers_changed(self):
        """The board edited a provider — keep the bottom strip honest."""
        self._log.append_log("SYS: Otak tugas berat diperbarui.")
        try:
            self._refresh_models_card()
        except Exception:                      # noqa: BLE001
            pass

    def _open_provider_settings(self):
        ov = ProviderSettingsOverlay(parent=self.centralWidget())
        ov.saved.connect(self._on_provider_saved)
        self._centre_overlay(ov)
        self._provider_overlay = ov            # keep a reference so it isn't GC'd

    def _on_provider_saved(self, pid: str):
        self._log.append_log(
            f"SYS: AI provider '{pid}' saved — heavy tasks route through it.")

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    # ── CONNECTIONS: the connector grid ──────────────────────────────────────

    def _plugin_name_for_ns(self, ns: str):
        for sec in (self.get_plugin_settings() or []):
            if (sec.get("namespace") or sec.get("plugin")) == ns:
                return sec.get("plugin") or ns
        return None

    def _connector_specs(self, search: str = "") -> list[dict]:
        """Everything that needs setting up, as card specs.

        Two sources, one shape: plugins that declared PLUGIN_SETTINGS, and the
        MCP servers in config/mcp.json. The panel draws whatever comes back, so
        neither source needs a UI change to appear. The Composio catalog gets
        the search text too — its search must cover all 1,600+ apps, never
        just the tiles the curated view happens to show.
        """
        from memory.config_manager import get_plugin_enabled
        specs: list[dict] = []

        for sec in (self.get_plugin_settings() or []):
            ns = sec.get("namespace") or sec.get("plugin")
            if not ns:
                continue
            fields = [f for f in (sec.get("fields") or [])
                      if isinstance(f, dict) and f.get("key")]
            values = sec.get("values") or {}
            # Only credential-shaped fields decide "set up or not": a toggle
            # with a default of True is not a connection.
            cred_keys = [f["key"] for f in fields
                         if (f.get("type") or "text").lower() in ("text", "password")]
            filled = [k for k in cred_keys if str(values.get(k) or "").strip()]
            plugin_name = sec.get("plugin") or ns
            enabled = get_plugin_enabled(plugin_name)
            if not enabled:
                status = "paused"
            elif cred_keys and not filled:
                status = "setup"
            else:
                status = "connected"
            action = sec.get("action") or {}
            specs.append({
                "kind": "plugin",
                "id": ns,
                "plugin": plugin_name,
                "name": _clean_title(sec.get("title")),
                "desc": _clean_title(sec.get("title")),
                "keywords": _connector_keywords(ns),
                "status": status,
                "fields": fields,
                "values": {**{f["key"]: f.get("default") for f in fields},
                           **values, "enabled": enabled},
                "action": action.get("run"),
                "action_label": action.get("label"),
            })

        for srv in (self.get_mcp_servers() if self.get_mcp_servers else []) or []:
            enabled = bool(srv.get("enabled", True))
            alive, tools = bool(srv.get("alive")), int(srv.get("tools") or 0)
            if not enabled:
                status, text = "paused", ""
            elif alive and tools:
                status, text = "connected", f"✓ {tools} tool"
            elif alive:
                status, text = "starting", ""
            else:
                status, text = "error", ""
            specs.append({
                "kind": "mcp",
                "id": srv.get("id", ""),
                "name": srv.get("id", ""),
                "desc": "Server tool MCP",
                "keywords": f"{_connector_keywords(srv.get('id', ''))} mcp server tool",
                "status": status,
                "status_text": text,
                "fields": _MCP_FIELDS,
                "values": {
                    "enabled": enabled,
                    "command": srv.get("command", ""),
                    "args": srv.get("args", []),
                    "env": [f"{k}={v}" for k, v in (srv.get("env") or {}).items()],
                    "startup_timeout_s": srv.get("startup_timeout_s", 60),
                    "call_timeout_s": srv.get("call_timeout_s", 300),
                    "max_tools": srv.get("max_tools", 0),
                },
            })

        specs.extend(self._composio_specs(search))
        return specs

    # ── CONNECTIONS: the Composio catalog (a third source of cards) ───────────

    def _composio_specs(self, search: str = "") -> list[dict]:
        """App cards from composio.dev. The bridge plugin owns fetching and
        caching; this only hands it the stored credentials and draws what
        comes back — a bad key becomes a card, never a crash."""
        mod = _composio_module()
        if mod is None:
            return []
        from memory.config_manager import get_plugin_config, get_plugin_enabled
        if not get_plugin_enabled("composio_connector"):
            return []
        cfg = get_plugin_config("composio")
        key = str(cfg.get("api_key") or "").strip()
        if not key:
            return []                   # the master card shows "＋" by itself
        user = str(cfg.get("user_id") or "aris").strip() or "aris"
        show_all = bool(cfg.get("show_all_apps", False))
        favs = cfg.get("favourite_apps")
        try:
            return mod.cards(key, user, show_all=show_all, favs=favs,
                             search=search) or []
        except Exception as e:                       # noqa: BLE001
            return [{"kind": "composio_app", "id": "composio:__error__",
                     "name": "Composio", "desc": f"Gagal membaca katalog: {e}",
                     "status": "error", "fields": [], "values": {}}]

    def _composio_connect(self, slug: str):
        mod = _composio_module()
        if mod is None:
            return False, "Plugin composio_connector tidak ditemukan."
        from memory.config_manager import get_plugin_config
        cfg = get_plugin_config("composio")
        key = str(cfg.get("api_key") or "").strip()
        if not key:
            return False, "API key Composio belum diisi — buka kartu Composio."
        user = str(cfg.get("user_id") or "aris").strip() or "aris"
        try:
            return True, mod.create_link(key, user, slug)
        except Exception as e:                       # noqa: BLE001
            return False, str(e)

    def _composio_poll(self, slug: str):
        mod = _composio_module()
        if mod is None:
            return "ERROR", "plugin hilang"
        from memory.config_manager import get_plugin_config
        cfg = get_plugin_config("composio")
        key = str(cfg.get("api_key") or "").strip()
        if not key:
            return "ERROR", "API key belum diisi"
        user = str(cfg.get("user_id") or "aris").strip() or "aris"
        try:
            conn = mod.connection_status(key, user, slug)
            return str(conn.get("status") or "NONE"), str(conn.get("id") or "")
        except Exception as e:                       # noqa: BLE001
            return "ERROR", str(e)

    def _composio_disconnect(self, conn_id: str) -> str:
        mod = _composio_module()
        if mod is None:
            return "Plugin composio_connector tidak ditemukan."
        from memory.config_manager import get_plugin_config
        cfg = get_plugin_config("composio")
        key = str(cfg.get("api_key") or "").strip()
        if not key:
            return "API key Composio belum diisi."
        try:
            return mod.delete_connection(key, conn_id) or ""
        except Exception as e:                       # noqa: BLE001
            return str(e)

    def _composio_refresh(self):
        mod = _composio_module()
        if mod is not None:
            try:
                mod.invalidate()
            except Exception:
                pass

    def _composio_state(self) -> dict:
        mod = _composio_module()
        if mod is None:
            return {}
        try:
            return mod.state() or {}
        except Exception:                            # noqa: BLE001
            return {}

    def _save_plugin_connector(self, ns: str, values: dict) -> str:
        """Save one plugin's card: enable state plus the declared fields."""
        from memory.config_manager import save_plugin_config, save_plugin_enabled
        values = dict(values or {})
        enabled = values.pop("enabled", None)
        if enabled is not None:
            plugin = self._plugin_name_for_ns(ns) or ns
            save_plugin_enabled(plugin, bool(enabled))
        save_plugin_config(ns, values)
        return ""

    def _open_connections(self):
        cw = self.centralWidget()
        callbacks = {
            "save_plugin": self._save_plugin_connector,
            "save_mcp":    self.save_mcp_server,
            "delete_mcp":  self.delete_mcp_server,
            "test_mcp":    self.restart_mcp_server,
            "reload":      self._connector_specs,
            "composio_connect":    self._composio_connect,
            "composio_poll":       self._composio_poll,
            "composio_disconnect": self._composio_disconnect,
            "composio_refresh":    self._composio_refresh,
            "composio_state":      self._composio_state,
        }
        ov = ConnectionsOverlay(self._connector_specs(), callbacks, parent=cw)
        ow = ConnectionsOverlay._OW
        oh = min(ConnectionsOverlay._OH, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._connections_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        # A deliberate press settles the question: whatever the video did, or is
        # about to do, the user has now said what they want.
        self._video_auto_muted = False
        self._set_muted(not self._muted)

    def _set_muted(self, muted: bool, note: str = ""):
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.hud.muted = muted
        self._style_mute_btn()
        self._refresh_footer_voice()
        self._refresh_safety_card()
        if muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted." + (f" {note}" if note else ""))
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active." + (f" {note}" if note else ""))

    def _style_mute_btn(self):
        # The dedicated rail mic button is gone (the footer's "Voice
        # listening" button took over), so this is just a state refresh —
        # kept because several call sites still poke it.
        self._refresh_footer_voice()

    def _send(self, source: str = "rail"):
        """One send path for both boxes. The rail box hands its text over
        directly; the ask bar route (`_send_center`) sets it first."""
        if source == "center":
            txt = self._input_center.text().strip()
            self._input_center.clear()
        else:
            txt = self._input.text().strip()
            self._input.clear()
        if not txt:
            return
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self._last_state  = state
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        self._refresh_footer_voice()
        self._refresh_safety_card()

    def _apply_model(self, name: str):
        """Header badge: the Live model this session is talking to. Thread-safe
        entry point is JarvisUI.set_model()."""
        name = str(name or "").strip()
        if name.startswith("models/"):
            name = name[len("models/"):]
        self._model_lbl.setText(f"AI MODEL: {name}" if name else "AI MODEL: —")

    def _apply_task_graph(self, label: str, status: str, ms: float) -> None:
        """Isi Live Task Graph dari kejadian nyata (thread-safe lewat signal).

        Sebelumnya grafik ini hanya diberi makan oleh teks log yang cocok
        dengan pola tertentu — dan pola itu hampir tidak pernah muncul, jadi
        panelnya selalu kosong meski pekerjaan sedang berjalan.
        """
        tg = getattr(self, "_task_graph", None)
        if tg is None:
            return
        try:
            if str(label or "").strip().lower() in ("clear", "__clear__"):
                tg.clear()
                return
            tg.mark(str(label or "")[:38], _tg_norm_status(status),
                    float(ms or 0.0) or None)
        except Exception:
            pass

    def _apply_task_progress(self, text: str):
        """One quiet line under the vitals: what the custom brain is doing.

        Thread-safe entry point is JarvisUI.task_progress(). Empty text clears
        it; the label keeps its height either way so the HUD never jumps."""
        lbl = getattr(self, "_task_lbl", None)
        if lbl is not None:
            lbl.setText(str(text or ""))

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        # Audit-3 25 Okt (KRITIS): dulu seluruh berkas DIGANTI dengan dua
        # field. Artinya memasukkan ulang kunci — misalnya setelah kunci
        # kedaluwarsa dan aplikasi meminta kunci baru — MENGHAPUS semua
        # provider, perangkat audio, dan pengaturan plugin pengguna.
        # Sekarang: gabungkan ke config yang ada, lalu tulis atomik.
        data = _read_full_config()
        if not isinstance(data, dict):
            data = {}
        data["gemini_api_key"] = key
        data["os_system"] = os_name
        from memory.config_manager import save_config as _save_cfg
        _save_cfg(data)
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "JARVIS") or "JARVIS"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        _hud_fonts()   # bundle-loaded Chakra Petch, when fonts/ ships it
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.show()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._mute_sig.emit(bool(v))

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_actions(self):
        return getattr(self._win, "get_actions", None)

    @get_actions.setter
    def get_actions(self, cb):
        self._win.get_actions = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def get_mcp_servers(self):
        return self._win.get_mcp_servers

    @get_mcp_servers.setter
    def get_mcp_servers(self, cb):
        self._win.get_mcp_servers = cb

    @property
    def save_mcp_server(self):
        return self._win.save_mcp_server

    @save_mcp_server.setter
    def save_mcp_server(self, cb):
        self._win.save_mcp_server = cb

    @property
    def delete_mcp_server(self):
        return self._win.delete_mcp_server

    @delete_mcp_server.setter
    def delete_mcp_server(self, cb):
        self._win.delete_mcp_server = cb

    @property
    def restart_mcp_server(self):
        return self._win.restart_mcp_server

    @restart_mcp_server.setter
    def restart_mcp_server(self, cb):
        self._win.restart_mcp_server = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for JARVIS's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        self._win._phone_sig.emit()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def set_model(self, name: str):
        """Thread-safe: show which Live model the session is using (header badge)."""
        self._win._model_sig.emit(str(name or ""))

    def refresh_model_ui(self):
        """Redraw the header chip + the models card from the saved config.

        Needed after a VOICE model switch: the config changed, but the chip
        is only repainted when the picker itself is used — which is how the
        header kept showing DeepSeek while the brain was already on GLM.
        """
        try:
            self._win._refresh_ui_sig.emit()
        except Exception:
            pass

    def task_progress(self, text: str):
        """Thread-safe: one line showing what the custom brain is doing.

        Empty string clears it. Deliberately a single line with a fixed
        height — the user asked for visible progress without the interface
        getting in the way."""
        try:
            self._win._task_sig.emit(str(text or ""))
        except Exception:
            pass

    def task_mark(self, label: str, status: str = "run", ms: float = 0.0):
        """Thread-safe: tambahkan satu langkah ke LIVE TASK GRAPH.

        Inilah "gambaran proses" yang diminta: tiap langkah nyata muncul
        sebagai node — ◆ berjalan, ✓ selesai — bukan satu baris teks kecil.
        """
        try:
            self._win._tg_sig.emit(str(label or ""), str(status or "run"),
                                   float(ms or 0.0))
        except Exception:
            pass

    def task_clear(self):
        """Thread-safe: kosongkan grafik sebelum pekerjaan baru dimulai."""
        try:
            self._win._tg_sig.emit("__clear__", "wait", 0.0)
        except Exception:
            pass

    # ── share screen (Jarvis watches + coaches) ─────────────────────────────

    def set_screen_share_handler(self, fn):
        """main.py registers what to do when the SHARE chip is pressed."""
        self._win.on_screen_share = fn

    def screen_share_show(self, on: bool):
        """Thread-safe: swap the HUD area to the screen panel (or back)."""
        try:
            self._win._screen_show_sig.emit(bool(on))
        except Exception:
            pass

    def screen_share_frame(self, jpeg: bytes, hint: str = ""):
        """Thread-safe: push one preview frame + the current coaching line."""
        try:
            self._win._screen_frame_sig.emit(bytes(jpeg), str(hint or ""))
        except Exception:
            pass

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def set_services(self, mcp_tools=None, plugins=None, brain=None, wake=None):
        """Thread-safe: the status board and the TOOLS card read this.

        Dulu metode ini memanggil set_services MainWindow LANGSUNG dari
        thread asyncio aplikasi — dan dipanggil ulang setiap reconnect. Di
        situ letak warning "QObject::setParent: new parent is in a different
        thread" yang berulang dan crash access violation yang mengikutinya."""
        try:
            self._win._services_sig.emit(mcp_tools, plugins, brain, wake)
        except Exception:
            pass

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "JARVIS should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                   list(findings or []), list(unclear or []))

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def show_video(self, source: str, title: str = "", muted: bool = True,
                   audio_source: str = "") -> None:
        """Thread-safe: play a video where the avatar normally is.

        `source` is a local path or a direct media URL. `audio_source` is for
        the case where the sound arrives as its own stream — YouTube serves no
        combined format any more — and when it is given the two are played
        together and kept in step.

        Muted by default, and that is a decision rather than a default: a
        soundtrack talking over JARVIS is the one way this could make the
        assistant worse. The user turns sound on from the header button or by
        asking, and closes it the same two ways.
        """
        self._win._video_open_sig.emit(str(source or ""), str(title or ""),
                                       bool(muted), str(audio_source or ""))

    def stop_video(self) -> None:
        """Thread-safe: close the video and give the HUD back to the avatar."""
        self._win._video_close_sig.emit()

    def set_video_muted(self, muted: bool) -> None:
        """Thread-safe: silence or unsilence whatever is playing."""
        self._win._video_mute_sig.emit(bool(muted))

    def video_is_playing(self) -> bool:
        return bool(self._win.video_is_playing())

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")