import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **                       kw)

    _subprocess.Popen = _Popen


# ── Console must survive non-UTF-8 code pages ────────────────────────────────
# Every status line in this file carries an emoji, and on a legacy Windows
# console the active code page is the system one — cp1254 in Turkey, cp1251 in
# Russia, cp932 in Japan. Printing an emoji there raises UnicodeEncodeError, and
# because most of these prints sit inside the receive loop it takes the session
# down on startup. Reconfiguring to UTF-8 with a replacement fallback costs
# nothing and makes the app launch the same way in every locale.
import sys as _sys

for _stream in ("stdout", "stderr"):
    try:
        _s = getattr(_sys, _stream, None)
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass          # pythonw / redirected pipes / anything exotic — never fatal

# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import faulthandler
import re
import threading
import time
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import sounddevice as sd
import numpy as np
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    save_session_summary, pop_last_session,
    search_memory, set_trim_notifier,
)

# The file-backed tools (open_app, web_search, browser_control, …) are no longer
# imported or declared here — they self-describe via a TOOL dict in their own
# actions/*.py file and are auto-discovered by core.action_loader at startup.
# Only tools that are tied to live-session state stay inline in this file
# (screen_process, close_camera, save_memory, manage_monitor, shutdown_jarvis,
# system_status).
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.background_monitor import (
    add_monitor, remove_monitor, list_monitors, check_all as monitor_check_all,
)
from actions.web_search        import _news as _fetch_news_sync
from memory.config_manager     import (
    get_brief_enabled, get_media_resolution, get_proactive_audio_enabled,
    get_push_to_talk_enabled, get_thinking_enabled, get_turn_tuning, get_voice,
    get_wake_word_enabled, save_wake_word_enabled,    get_input_device, get_output_device,
    get_wake_threshold,
)
from core                     import gemini as _gemini
from core.plugin_loader        import discover_plugins
from core                      import undo as undo_stack
from core                      import confirm as confirm_gate
from core                      import audio_devices
from core.action_loader        import discover_actions
from core.mcp_manager          import MCPManager
from core.skills               import load_skills
from core                      import macros as _macros
from core.echo                 import EchoGuard
from core.custom_brain         import CustomBrain
from core.receipt              import (compose_receipt, landed_line,
                                       LandedRing, deferred_block,
                                       DEFERRED_MAX)
from core.bargein              import compose_cut_note
from core.viseme               import VisemeStream
from core.wake_word            import (
    WakeWordDetector, is_ready as wake_is_ready, install_and_download as wake_install,
)
from config                    import get_flags

# How long the assistant stays awake with no user speech before it auto-sleeps
# again (wake-word mode only). Long on purpose: a pause of a few minutes in the
# middle of a conversation is not the end of one — the wake word should be
# needed again only after half an hour of genuine silence.
WAKE_SLEEP_TIMEOUT = 1800.0   # seconds (30 minutes)

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
# The conversation's model. A NAME, not a decision: the ladder lives in
# core/gemini.py and this is only whichever rung is currently in use, kept here
# as a module attribute because plugins read it (chat_takeover asks main for it
# so that upgrading the assistant upgrades the plugin too).
#
# It is reassigned on every connect, so a model that runs out of quota is
# stepped over and the assistant keeps talking instead of failing to start.
LIVE_MODEL          = "models/gemini-3.1-flash-live-preview"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# ── hybrid engine: Gemini is the mouth, the custom API is the brain ─────────
#
# voice_brain = "custom" in config/api_keys.json swaps the thinking model:
# the Live session keeps exactly one tool, `brain`, and speaks whatever comes
# back. Everything a real assistant does — deciding, tool calls, composing
# the answer — moves to the user's own provider. See core/custom_brain.py.
_BRAIN_IDLE_TIMEOUT = 300.0  # detik tanpa kemajuan SEBELUM dinyatakan macet.
                             # 120 s terlalu pendek: satu panggilan model besar
                             # untuk satu langkah coding bisa >2 menit, dan
                             # memutusnya membuat pekerjaan sehat terlihat
                             # seperti provider mati (laporan 23 Okt).
_BRAIN_HARD_CAP = 1800.0     # absolute ceiling for one turn (30 min) — a last
                             # resort, not the normal path: a real project may
                             # legitimately run for many minutes.

# Sabar untuk SATU panggilan provider. 120 s (nilai config lama) memotong
# model yang masih mengerjakan satu langkah besar. Batas ini hanya untuk
# mencegah panggilan menggantung selamanya — bukan untuk menilai kecepatan.
_BRAIN_CALL_MIN_S = 300.0
_BRAIN_CALL_MAX_S = 600.0

# Sesi Live punya BATAS DURASI dari server (GoAway 1008: "session duration
# limit"). Itu sebabnya "tugas yang agak banyak selalu putus" — bukan karena
# mic, bukan karena loop penerima (keduanya sudah diuji: sesi tetap hidup
# 100 s tanpa audio, dan tetap hidup walau loop penerima diblokir 70 s).
# Karena batasnya tidak bisa dinaikkan, sesi DIPUTAR sendiri lebih dulu saat
# sedang menganggur, sehingga pemutusan terjadi di titik aman — bukan di
# tengah pekerjaan.
_SESSION_SOFT_LIMIT_S = 480.0   # 8 menit: di bawah batas server (~10 menit)


def _should_rotate_session(age: float, busy: bool,
                           limit: float = _SESSION_SOFT_LIMIT_S) -> bool:
    """True bila sesi Live sebaiknya disambung ulang SEKARANG.

    Hanya saat menganggur: memutar sesi di tengah giliran (otak bekerja,
    JARVIS berbicara, atau tool berjalan) akan memotong pekerjaan yang justru
    sedang ditunggu pengguna.
    """
    if busy:
        return False
    return age >= limit


def _job_step_should_log(text: str, last_text: str, last_ts: float,
                         now: float, gap: float = 2.0) -> bool:
    """Langkah kerja latar dicatat ke activity log tanpa membanjirinya."""
    if str(text or "") != str(last_text or ""):
        return True
    return (now - float(last_ts or 0.0)) >= gap


# ── Efisiensi token otak custom ───────────────────────────────────────────
# Audit 23 Okt: 55 deklarasi tool = ~16.000 token dan dikirim ULANG setiap
# langkah (maks 12) — satu tugas 8 langkah ~170.000 token. Tool menyumbang
# 77% dari tiap panggilan. Jadi yang dikirim rutin dipangkas ke tool yang
# benar-benar dipakai (bukti: audit.jsonl 1.258 entri), dan sisanya tetap
# terjangkau lewat `search_tools` — kemampuan tidak berkurang, payload turun.
_CORE_TOOLS = (
    "file_controller", "code_helper", "dev_agent", "run_command",
    "web_search", "browser_control", "computer_control", "open_app",
    "send_message", "docx_writer", "excel_writer", "composio_actions",
    "composio_connector", "recall_memory", "save_memory", "model_status",
    "switch_model", "screen_process", "weather_report", "system_status",
    "cloud_agent", "video_player",
)


def _core_decls(decls) -> list:
    """Hanya deklarasi tool inti — sisanya on-demand lewat search_tools."""
    core = set(_CORE_TOOLS)
    return [d for d in decls
            if isinstance(d, dict) and d.get("name") in core]


def _search_decls(decls, query: str, limit: int = 5) -> list:
    """Cari deklarasi berdasarkan nama/deskripsi (untuk search_tools)."""
    q = str(query or "").strip().lower()
    if not q:
        return []
    hits = []
    for d in decls:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name", "")).lower()
        desc = str(d.get("description", "")).lower()
        if q in name or q in desc:
            hits.append(d)
            if len(hits) >= limit:
                break
    return hits


def _brain_max_steps_for(kind: str, base: int) -> int:
    """Jumlah langkah sesuai jenis giliran.

    Percakapan pendek tidak perlu 12 langkah; tiap langkah mengirim ulang
    system + deklarasi tool, jadi memotong langkah memotong token. Tugas
    tulis tetap memakai batas penuh supaya tidak berhenti di tengah.
    """
    try:
        b = int(base)
    except (TypeError, ValueError):
        b = 12
    if kind == "work_request":
        return max(4, b)
    if kind == "confirmation":
        return 3
    if kind == "recall":
        return 6
    if kind in ("small_talk", "farewell", "dictation"):
        return 2
    return max(8, min(b, 12))


# Kata kunci → tool yang dipangkas dari daftar inti. Deterministik: kalau
# ucapan pengguna menyebut "pomodoro", tool pomodoro DILAMPIRKAN untuk giliran
# itu saja. Diuji 23 Okt: tanpa ini model (river) memalsukan pomodoro lewat
# run_command pada 2 dari 3 percobaan — kemampuan tidak boleh bergantung pada
# kepatuhan model.
_TOOL_HINTS = {
    "pomodoro": ("pomodoro",),
    "timer": ("pomodoro",),
    "fokus": ("pomodoro",),
    "quiz": ("quiz",),
    "kuis": ("quiz",),
    "pengingat": ("reminder", "water_reminder"),
    "ingatkan": ("reminder",),
    "minum": ("water_reminder",),
    "air": ("water_reminder",),
    "struk": ("receipt_ledger",),
    "receipt": ("receipt_ledger",),
    "harga": ("price_tracker",),
    "notulen": ("meeting_notetaker",),
    "rapat": ("meeting_notetaker",),
    "kalender": ("calendar_assistant",),
    "jadwal": ("calendar_assistant",),
    "rumah": ("home_automation",),
    "lampu": ("home_automation",),
    "game": ("game_updater",),
    "review": ("document_review",),
    "dokumen": ("document_review",),
    "pdf": ("file_processor",),
    "youtube": ("youtube_video",),
    "video": ("video_player", "youtube_video"),
    "penerbangan": ("flight_finder",),
    "tiket": ("flight_finder",),
    "jurnal": ("voice_journal",),
    "makro": ("voice_macros",),
    # sinonim perintah RINGAN (buka/lihat/putar/refresh/cari)
    "refresh": ("browser_control",),
    "segarkan": ("browser_control",),
    "reload": ("browser_control",),
    "muat ulang": ("browser_control",),
    "putar": ("video_player", "youtube_video"),
    "play": ("video_player", "youtube_video"),
    "berita": ("web_search",),
    "news": ("web_search",),
    "cuaca": ("weather_report",),
    "weather": ("weather_report",),
    "status": ("system_status",),
    "buka": ("open_app",),
    "open": ("open_app",),
}


def _hinted_tool_names(utterance: str) -> set[str]:
    """Nama tool yang relevan dengan ucapan ini (untuk dilampirkan)."""
    low = str(utterance or "").lower()
    out: set[str] = set()
    if not low:
        return out
    for word, names in _TOOL_HINTS.items():
        if word in low:
            out.update(names)
    return out


def _hinted_decls(decls, utterance: str) -> list:
    """Deklarasi tool tambahan yang relevan, hanya bila disebut pengguna."""
    want = _hinted_tool_names(utterance)
    if not want:
        return []
    core = set(_CORE_TOOLS)
    return [d for d in decls
            if isinstance(d, dict) and d.get("name") in want
            and d.get("name") not in core]


def _brain_extra_notes() -> list[str]:
    """Catatan tetap yang WAJIB ikut ke system prompt otak.

    Dikumpulkan di satu tempat supaya bisa diuji: aturan yang hilang tanpa
    ketahuan adalah cara paling halus untuk membuat JARVIS "bodoh" lagi.
    """
    return [_HIDDEN_TOOLS_NOTE, _VOICE_CHANNEL_NOTE, _LANGUAGE_NOTE]


def _provider_notice_enabled() -> bool:
    """Bolehkah kegagalan provider diumumkan lewat SUARA?

    Bawaannya TIDAK: pengumuman itu memicu satu giliran otak tambahan
    (~21.000 token) hanya untuk memberi tahu bahwa provider lain menjawab.
    Log + badge sudah menyampaikan hal yang sama tanpa biaya. Nyalakan
    dengan "voice.provider_notice": true di config/voice_flags.json.
    """
    try:
        return bool(get_flags().get("voice.provider_notice", True))
    except Exception:                            # noqa: BLE001
        return True


# ── Pembagian kerja: klaim tindakan + sakelar mode ────────────────────────
# 23 Okt. Dua lubang yang tersisa setelah routing A/B/C:
#  1. Gemini bisa MENJAWAB sendiri tugas berat tanpa memanggil tool, lalu
#     mengaku sudah mengerjakan — guard lama hanya menangkap "sudah/telah".
#  2. Bos perlu bisa memaksa semua pekerjaan ke otak custom lewat suara.
_ACTION_VERBS = (
    "buat", "membuat", "dibuat", "buatkan",
    "edit", "mengedit", "diedit",
    "ubah", "mengubah", "diubah",
    "hapus", "menghapus", "dihapus",
    "kirim", "mengirim", "dikirim", "kirimkan",
    "jalankan", "menjalankan", "dijalankan",
    "susun", "menyusun", "disusun",
    "rekap", "merekap", "direkap",
    "rangkum", "merangkum", "dirangkum",
    "rapikan", "merapikan", "dirapikan",
    "pindah", "memindahkan", "dipindahkan",
    "salin", "menyalin", "disalin",
    "simpan", "menyimpan", "disimpan",
    "pasang", "memasang", "dipasang",
    "unduh", "mengunduh", "diunduh",
    # Inggris (permintaan bos: "selipkan bahasa Inggris biar jaga-jaga")
    "create", "created", "build", "built", "edit", "edited",
    "send", "sent", "save", "saved", "write", "wrote",
    "update", "updated", "delete", "deleted", "fix", "fixed",
    "make", "made", "add", "added", "remove", "removed",
    "install", "installed", "download", "downloaded",
    "upload", "uploaded", "refactor", "deploy", "merge",
    "convert", "translate", "summarize", "rewrite", "modify", "modified",
)
_CLAIMS_ACTION = re.compile(
    r"\b(saya|aku|i)\b[^.?!\n]{0,45}?\b(" + "|".join(_ACTION_VERBS) + r")\b",
    re.IGNORECASE)

_NEGATION = re.compile(r"\b(jangan|jgn|gak|nggak|tidak|bukan)\b", re.IGNORECASE)
_FORCE_CMD = re.compile(
    r"\b(alihkan|serahkan|pindahkan|limpahkan|kerjakan)\b[^.?!\n]{0,40}"
    r"\b(custom|otak)\b"
    r"|\b(pakai|gunakan|pilih)\b[^.?!\n]{0,25}\b(otak\s+custom|custom\s+api|custom)\b"
    r"[^.?!\n]{0,25}\b(semua|semuanya|saja|seluruh)\b",
    re.IGNORECASE)
_DEFAULT_CMD = re.compile(
    r"\b(mode|kembali|balik|balikin)\b[^.?!\n]{0,25}"
    r"\b(default|normal|biasa|otomatis|otomatik)\b",
    re.IGNORECASE)


def claims_action(text: str) -> bool:
    """True bila jawaban MENGKLAIM tindakan yang dilakukan si pembicara.

    Dipakai untuk menangkap celah "Gemini menjawab sendiri tugas berat":
    "Saya buatkan filenya seperti ini" adalah klaim tindakan meskipun tidak
    memakai kata "sudah". Kalimat penjelasan ("berikut cara membuat file")
    tidak boleh ikut tertangkap, karena itu jawaban yang sah.
    """
    return bool(_CLAIMS_ACTION.search(str(text or "")))


def _mode_command(utterance: str) -> str | None:
    """'force' | 'default' | None — perintah kendali mode lewat suara.

    Deterministik dan sengaja sempit: salah mendeteksi berarti seluruh
    percakapan berpindah jalur tanpa diminta.
    """
    low = str(utterance or "").strip()
    if not low:
        return None
    if _NEGATION.search(low):
        return None                      # "jangan alihkan..." bukan perintah
    if _DEFAULT_CMD.search(low):
        return "default"
    if _FORCE_CMD.search(low):
        return "force"
    return None


def _should_force_brain(force: bool, name: str, is_builtin: bool) -> bool:
    """Saat mode paksa, tool ringan pun dialihkan ke otak.

    Builtin (recall_memory, model_status, …) tidak dialihkan: itu pembukuan
    aplikasi, bukan pekerjaan, dan mengalihkannya hanya menambah satu putaran.
    """
    if not force:
        return False
    if is_builtin:
        return False
    return str(name) not in ("brain", "custom_brain")


# ── Tawaran sebelum mengambil alih mouse/keyboard ─────────────────────────
# Keputusan bos 23 Okt: aksi mouse/keyboard yang BERAT (mengetik, menempel,
# rangkaian) dialihkan ke otak custom supaya tidak salah keputusan — tetapi
# Gemini harus MENAWARKAN dulu, dan pengguna yang memilih. Tanpa tawaran,
# perpindahan model terjadi di belakang layar dan terasa seperti salah paham.
_CC_HEAVY = {
    "type", "smart_type", "paste", "copy", "drag", "hotkey",
    "clear_field", "screen_click", "random_data", "user_data",
}
_CC_OFFER_TTL = 120.0          # tawaran basi setelah 2 menit


def _cc_is_heavy(args) -> bool:
    """True bila aksi computer_control ini termasuk "tugas berat".

    Klik tunggal dengan target jelas tetap ringan. Klik TANPA target
    dianggap berat: klik buta adalah klik yang paling berbahaya.
    """
    a = str((args or {}).get("action") or "").strip().lower()
    if a in _CC_HEAVY:
        return True
    if a in ("click", "double_click", "right_click"):
        target = str((args or {}).get("text")
                     or (args or {}).get("target") or "").strip()
        return not target
    return False


def _cc_choice(utterance: str) -> str | None:
    """'brain' | 'gemini' | None — pilihan bos atas tawaran tadi."""
    low = str(utterance or "").strip().lower()
    if not low:
        return None
    if _NEGATION.search(low):
        return None
    if any(k in low for k in ("otak", "custom")):
        return "brain"
    if any(k in low for k in ("gemini", "kamu saja", "kamu aja",
                              "kamu yang", "sendiri", "kamu kerjakan")):
        return "gemini"
    return None


def _cc_offer_fresh(ts: float, now: float,
                    ttl: float = _CC_OFFER_TTL) -> bool:
    """Tawaran hanya berlaku sebentar — supaya pilihan lama tak nyangkut."""
    try:
        t = float(ts or 0.0)
    except (TypeError, ValueError):
        return False
    return t > 0.0 and (now - t) <= ttl


# ── Kejujuran tampilan, pemulihan koneksi, catatan pekerjaan ──────────────
# 24 Okt. Tiga hal yang membuat JARVIS terasa "tidak sinkron":
#  1. Badge menampilkan model Gemini / model terpilih, bukan otak yang
#     benar-benar menjawab — jadi saat GLM mengambil alih, UI tetap "claude".
#  2. Jalur kirim audio menandai koneksi mati lalu membuang frame tanpa
#     pernah meminta sambung ulang → JARVIS diam sampai di-restart.
#  3. Hasil pekerjaan latar tidak dicatat ke riwayat, jadi otak menjawab
#     "belum dikerjakan" padahal berkasnya sudah ada.


# Tool yang TIDAK boleh dialihkan ke otak: pembukuan aplikasi, bukan pekerjaan.
_BUILTIN_TOOLS = {"brain", "custom_brain", "save_memory", "recall_memory",
                  "model_status", "undo", "screen_process", "close_camera",
                  "screen_share", "switch_model", "system_status",
                  "manage_monitor", "shutdown_jarvis", "agent_run",
                  "reload_plugins"}

# ── Ucapan sepele tidak boleh dibayar 12.000 token ────────────────────────
# 24 Okt: kuota Claude habis oleh percakapan biasa. Sebabnya SETIAP ucapan —
# termasuk "oke" dan "habis limitnya" — dikirim ke otak sebagai satu
# panggilan penuh (system 4.900 + tool 7.000 token). Coding agent tidak
# begitu: ia memanggil model saat ada tugas.
#
# Aturannya deterministik, bukan penilaian model:
#   small_talk / farewell        -> mulut menjawab sendiri
#   unclear pendek tanpa tanya   -> komentar, mulut menjawab sendiri
#   "oke/siap/lanjut/ya/gas"     -> PERINTAH implisit, tetap ke otak
_IMPLICIT_CMD = {"oke", "ok", "okey", "okay", "siap", "lanjut", "lanjutkan",
                 "ya", "iya", "baik", "gas", "ayo", "boleh", "silakan"}


def _is_small_turn(utterance: str) -> bool:
    """True bila ucapan ini tidak layak dibayar satu panggilan otak penuh."""
    text = " ".join(str(utterance or "").split())
    if not text:
        return False
    kind = classify_turn(text)
    if kind in ("small_talk", "farewell"):
        return True
    if kind != "unclear":
        return False
    # "cek jadwal saya" itu PERINTAH, bukan komentar — jangan dijawab mulut.
    if is_read_only(text):
        return False
    words = text.split()
    if len(words) > 6 or "?" in text:
        return False
    bare = {w.strip(".,!?;:").lower() for w in words}
    if bare & _IMPLICIT_CMD:
        return False          # "oke" / "lanjut" = perintah, bukan obrolan
    return True


# ── Tiga perbaikan dari log 24 Okt ────────────────────────────────────────
# 1. "switch model ke X" dijawab sendiri oleh mulut ("tidak bisa") padahal
#    tool switch_model ADA. Gerbang hanya berjalan bila tool dipanggil, jadi
#    perintah kendali harus ditangkap dari TRANSKRIP — deterministik.
_SWITCH_VERB = re.compile(r"\b(switch|ganti|pindah|ubah|alihkan)\b",
                          re.IGNORECASE)
_SWITCH_NOUN = re.compile(r"\b(model|otak)\b", re.IGNORECASE)
_SWITCH_TARGET = re.compile(
    r"\b(?:ke|menjadi|jadi|to)\s+"
    r"([A-Za-z0-9][\w./\- ]{2,44}?)"
    r"(?:\s+(?:dari|lewat|from|di)\s+([A-Za-z0-9][\w.\-]{1,20}))?"
    r"\s*[.?!]?\s*$",
    re.IGNORECASE)


# 25 Okt — perintah kendali harus berbentuk IMPERATIF, bukan sekadar memuat
# kata kuncinya. Kejadian nyata: "berarti tadi selama mode curhat kamu nggak
# bisa nyimpen di memori ya?" mengaktifkan mode curhat hanya karena ada kata
# "mode curhat" — padahal itu pertanyaan. Sama kelasnya dengan bug Gmail.
_IMPERATIVE_HEAD = {
    "masuk", "masukkan", "aktifkan", "nyalakan", "pakai", "gunakan", "mulai",
    "matikan", "keluar", "stop", "selesai", "hentikan", "buka", "aktif",
}
_META_TALK = re.compile(
    r"\b(tadi|sebelumnya|kamu bilang|katanya|berarti|maksudnya|apakah|"
    r"apa itu|gimana|bagaimana|kenapa|mengapa|bisa nggak|bisa ga|"
    r"nggak bisa|tidak bisa|jelaskan|maksud kamu|nanti|seandainya|andai|"
    r"besok|kemarin)\b",
    re.IGNORECASE)


def _command_shaped(text: str, extra_verbs=None) -> bool:
    """True bila kalimat ini berbentuk PERINTAH, bukan pembicaraan tentangnya.

    Tiga tingkat, dari paling pasti:
      1. kata kerja imperatif di tiga kata pertama ("masuk mode curhat",
         "jarvis tolong aktifkan mode curhat") -> perintah;
      2. ada penanda membahas/menanyakan ("tadi", "apakah", "?", "nanti") ->
         bukan perintah, walau kata kerjanya ada;
      3. kata kerja imperatif di mana pun, tanpa penanda di atas ->
         perintah ("saya mau kamu masuk mode curhat sekarang");
      4. sisanya: hanya ucapan pendek (<= 4 kata) yang dianggap perintah.
    """
    raw = str(text or "")
    words = re.findall(r"[a-z]+", raw.lower())
    heads = _IMPERATIVE_HEAD | set(extra_verbs or ())
    # Penanda membahas/melaporkan/merencanakan menang lebih dulu: "nanti kita
    # masuk mode curhat ya" memuat kata "masuk", tetapi itu rencana, bukan
    # perintah sekarang.
    if _META_TALK.search(raw):
        return False
    if any(w in heads for w in words[:3]):
        return True
    if "?" in raw:
        return False
    if any(w in heads for w in words):
        return True
    return len(words) <= 4


def _vent_ambiguous(text: str) -> bool:
    """True bila ucapan MENYEBUT mode tetapi belum bisa diputuskan.

    Hanya di zona ini model murah dipanggil. Pertanyaan yang jelas membahas
    ("apakah ... ?") sengaja TIDAK masuk sini — itu sudah pasti bukan perintah.
    """
    t = " ".join(str(text or "").split())
    if not t or _NEGATION.search(t):
        return False
    if not re.search(r"\b(curhat|mode)\b", t, re.IGNORECASE):
        return False
    if "?" in t and _META_TALK.search(t):
        return False
    return _vent_command(t) is None


_VERDICT_WORDS = ("perintah", "bahas")


def _parse_verdict(raw: str) -> str | None:
    """Baca jawaban model: 'perintah' | 'bahas' | None (ngawur = None)."""
    t = str(raw or "").strip().lower()
    if not t:
        return None
    head = t[:40]
    for w in _VERDICT_WORDS:
        if w in head:
            return w
    return None


_VENT_ON = re.compile(
    r"\b(mode\s+curhat|mode\s+cerita|curhat\s+mode|mode\s+ngobrol)\b"
    r"|\b(aktifkan|masuk|pakai|gunakan)\b[^.?!\n]{0,20}\b(curhat|cerita)\b",
    re.IGNORECASE)
_VENT_OFF = re.compile(
    r"\b(mati(kan)?|stop|keluar|selesai)\b[^.?!\n]{0,20}\b(curhat|cerita)\b"
    r"|\bmode\s+(default|normal|biasa)\b",
    re.IGNORECASE)


def _vent_context_text(sessions, limit_chars: int = 3000) -> str:
    """Blok ringkasan curhat sebelumnya, untuk disuntik saat mode curhat aktif.

    Diminta bos 25 Okt: setiap masuk mode curhat, JARVIS harus sudah tahu apa
    yang pernah diceritakan — supaya pengguna tidak mengulang dari awal.
    Diberi penanda JANGAN DIBACAKAN karena ini latar, bukan kalimat untuk
    diucapkan.
    """
    rows: list[str] = []
    for e in (sessions or []):
        if not isinstance(e, dict):
            continue
        s = " ".join(str(e.get("summary") or "").split())
        if not s:
            continue
        rows.append(f"- {e.get('date', '')}: {s}")
    if not rows:
        return ""
    picked: list[str] = []
    total = 0
    for r in reversed(rows):              # yang terbaru didahulukan
        if total + len(r) > limit_chars:
            break
        picked.append(r)
        total += len(r)
    picked.reverse()
    return ("[KONTEKS CURHAT SEBELUMNYA - JANGAN DIBACAKAN]\n"
            "Ini ringkasan cerita yang pernah disampaikan pengguna. Pakai "
            "sebagai latar supaya kamu tidak menyuruhnya mengulang. Jangan "
            "bacakan daftar ini dan jangan sebut kata KONTEKS.\n"
            + "\n".join(picked) + "\n<<SELESAI KONTEKS>>")


def _control_once(handled: str, utterance: str) -> bool:
    """True hanya bila teks ini BELUM ditangani sebagai perintah kendali.

    `plugin_say()` menyuntik kalimat ke sesi Live dan itu menghasilkan
    `turn_complete` baru. Tanpa penjaga ini, hook membaca ucapan yang sama
    berulang kali dan berbicara tanpa henti (kejadian 24 Okt: 8x).
    """
    t = " ".join(str(utterance or "").split())
    if not t:
        return False
    return t != " ".join(str(handled or "").split())


def _vent_command(utterance: str) -> str | None:
    """'on' | 'off' | None — perintah mode curhat.

    Mode ini menjawab kebutuhan yang tidak bisa diselesaikan daftar kata:
    saat pengguna bercerita, TIDAK ada kata di dalam cerita yang boleh
    memicu pengalihan ke otak. Pengguna yang menyalakannya, bukan tebakan.
    """
    text = " ".join(str(utterance or "").split())
    if not text:
        return None
    if _NEGATION.search(text):
        return None
    # Pertanyaan/laporan yang menyebut mode bukan perintah. Veto ini berlaku
    # lebih dulu supaya "apakah sudah keluar dari mode curhat?" tidak
    # benar-benar mengeluarkan pengguna dari mode.
    if "?" in text and _META_TALK.search(text):
        return None
    if not _command_shaped(text):
        return None
    if _VENT_OFF.search(text):
        return "off"
    if _VENT_ON.search(text):
        return "on"
    return None


def _switch_command(utterance: str) -> dict | None:
    """{"model": …, "provider": …} bila ucapan adalah perintah ganti model."""
    text = " ".join(str(utterance or "").split())
    if not text:
        return None
    if _NEGATION.search(text):
        return None                      # "jangan ganti model"
    # 25 Okt — "apakah tadi kamu ganti model ke claude?" adalah pertanyaan,
    # bukan perintah. Veto yang sama seperti mode curhat.
    if "?" in text and _META_TALK.search(text):
        return None
    if not _command_shaped(text, {"switch", "ganti", "pindah", "ubah",
                                  "alihkan", "gantikan", "rubah"}):
        return None
    if not _SWITCH_VERB.search(text) or not _SWITCH_NOUN.search(text):
        return None
    m = _SWITCH_TARGET.search(text)
    if not m:
        return None
    model = (m.group(1) or "").strip(" .,")
    prov = (m.group(2) or "").strip(" .,")
    if len(model) < 3:
        return None
    return {"model": model, "provider": prov}


def _is_duplicate_job(prev_sig: str, sig: str, age_s: float,
                      window: float = 60.0) -> bool:
    """True bila pekerjaan yang PERSIS sama baru saja dijalankan.

    Kejadian 24 Okt: dua email identik terkirim karena otak tidak tahu job
    sebelumnya sudah selesai. Ini penjaga terakhir di sisi aplikasi.
    """
    if not prev_sig or not sig or prev_sig != sig:
        return False
    try:
        return float(age_s) <= float(window)
    except (TypeError, ValueError):
        return False


def _badge_label(brain_mode: bool, pid: str, model: str,
                 last_pid: str, health) -> str:
    """Label badge yang JUJUR: siapa yang benar-benar menjawab.

    Mode hibrida: otak custom yang menjawab, jadi badge menampilkan model
    otak — dan provider yang terakhir berhasil, bukan yang terpilih.
    Mode live: Gemini yang menjawab.
    """
    if not brain_mode:
        return "gemini live"
    last_pid = str(last_pid or "")
    if last_pid:
        for h in (health or []):
            if str(h.get("pid")) == last_pid:
                m = str(h.get("model") or "").strip()
                return f"{m} ({last_pid})" if m else last_pid
        return last_pid
    m = str(model or "").strip()
    return f"{m} ({pid})" if (m and pid) else (m or pid or "—")


def _should_ask_reconnect(live_down: bool, already: bool, now: float,
                          last: float, gap: float = 5.0) -> bool:
    """Minta sambung ulang sekali setiap 5 detik — jangan membanjiri loop."""
    if not live_down:
        return False
    if already and (now - float(last or 0.0)) < gap:
        return False
    return True


def _fallback_summary(lines, limit: int = 300) -> str:
    """Ringkasan cadangan saat model tidak bisa meringkas (kuota habis).

    Yang disimpan adalah ucapan terakhir pengguna apa adanya. Itu bukan
    ringkasan yang bagus, tetapi masih bisa ditemukan `recall_memory` —
    sedangkan kehilangan seluruh sesi tidak bisa dipulihkan sama sekali.
    """
    said: list[str] = []
    for ln in (lines or []):
        s = " ".join(str(ln or "").split())
        low = s.lower()
        if low.startswith("user:") or low.startswith("you:"):
            said.append(s.split(":", 1)[1].strip())
    if not said:
        return ""
    text = " | ".join(said[-2:])
    text = " ".join(text.split())
    return (text[:limit].rstrip() + "…") if len(text) > limit else text


def _job_record(title: str, summary: str) -> str:
    """Satu baris riwayat pekerjaan yang bisa dibaca otak DAN mulut.

    Ditulis ke `_session_log` dengan awalan nama asisten, karena itulah yang
    diurai `_recent_dialogue()` menjadi konteks percakapan. Tanpa ini, otak
    tidak tahu pekerjaan sebelumnya sudah selesai (kejadian 24 Okt: berkas
    HTML sudah ada, otak menjawab "belum dibuat").
    """
    t = " ".join(str(title or "").split())[:80]
    d = " ".join(str(summary or "").split())[:220]
    return f"[PEKERJAAN SELESAI] {t} — {d}"


def _brain_call_timeout(configured) -> float:
    """Batas waktu satu panggilan provider, dengan kesabaran minimum 300 s.

    Nilai dari config tetap dihormati hanya bila LEBIH BESAR: 120 s terbukti
    memutus pekerjaan yang masih berjalan, sedangkan panggilan yang benar-
    benar mati gagal jauh lebih cepat dari 300 s (koneksi putus = milidetik).
    """
    try:
        v = float(configured or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v <= 0:
        return _BRAIN_CALL_MIN_S
    return max(_BRAIN_CALL_MIN_S, min(_BRAIN_CALL_MAX_S, v))


def _brain_stalled(now: float, last_progress: float, in_call: bool,
                   limit: float = _BRAIN_IDLE_TIMEOUT) -> bool:
    """True hanya bila otak benar-benar BERHENTI, bukan sedang menunggu.

    Selama satu panggilan provider berjalan (`in_call`), diam adalah normal:
    model besar bisa butuh menit untuk satu langkah. Yang dicari adalah
    provider yang tidak pernah kembali — bukan yang lambat.
    """
    if in_call:
        return False
    return (now - last_progress) > limit

# The brain's tool set is the ~50 core tools (built-ins, actions, plugins).
# The 207 MCP tools (REA, reverse-engineering) carry long descriptions and
# push one request past 400 KB — slow to send and a standing invitation for
# a gateway to fail mid-request. So they are not sent by default: the brain
# gets this one meta-tool instead, searches on demand, and the tools it finds
# are attached to the NEXT request only. Set brain_mcp_tools=true in the
# config to go back to sending all of them every turn.
_VOICE_CHANNEL_NOTE = (
    "[SUARA, BUKAN TEKS]\n"
    "Kamu mendengar pengguna lewat MIKROFON: setiap ucapan pengguna yang kamu "
    "terima adalah hasil transkripsi suara, bukan pesan teks. Jangan pernah "
    "menjawab bahwa kamu hanya membaca teks, dan jangan menyuruh pengguna "
    "menyalakan mikrofon — mikrofonnya sudah menyala."
)

# 24 Okt — pengguna berbicara Bahasa Indonesia, tetapi transkripsi suara
# kadang keluar sebagai bahasa lain (log: "Vou levar para um que eu não
# conheço", "Baya tu error, ¿eh?") dan model lalu MENJAWAB dalam bahasa itu
# ("Ah, excuse me! I was checking something on the screen"). Aturan tetap:
# Indonesia adalah bahasa bawaan; bahasa lain hanya kalau pengguna benar-benar
# memintanya. Ini mengunci bahasa, bukan mengatur isi jawaban.
_LANGUAGE_NOTE = (
    "[BAHASA]\n"
    "Jawab SELALU dalam Bahasa Indonesia yang wajar. Transkripsi yang terbaca "
    "sebagai bahasa lain (Spanyol/Portugis/Inggris) adalah salah dengar, bukan "
    "permintaan berganti bahasa — jangan membalas dalam bahasa itu. Pindah "
    "bahasa hanya bila pengguna memintanya dengan jelas."
)

_HIDDEN_TOOLS_NOTE = (
    "[TOOL TERSEMBUNYI]\n"
    "Daftar tool-mu sengaja pendek supaya hemat. Kalau kamu butuh "
    "kemampuan yang TIDAK terlihat di daftar (Excel, quiz, pomodoro, "
    "pengingat air, pencatat struk, pelacak harga, notulen rapat, "
    "home automation, analisis binary, dan sejenisnya), panggil "
    "`search_tools` dengan kata kunci lebih dulu. JANGAN menulis "
    "skrip atau memakai tool lain sebagai pengganti, dan jangan "
    "mengaku pekerjaan selesai sebelum tool yang tepat dijalankan."
)

_SEARCH_TOOLS = {
    "name": "search_tools",
    "description": (
        "Cari tool yang TIDAK ada di daftar pendekmu: analisis binary, "
        "reverse-engineering (decompile, disassemble, Ghidra, strings, "
        "imports, breakpoint), plugin khusus (quiz, pomodoro, pengingat air, "
        "pencatat struk, harga, Excel, notulen rapat, home automation, "
        "game updater), atau tool lain yang tidak terlihat. Kirim kata kunci; "
        "tool yang cocok tampil LENGKAP dengan parameternya dan bisa langsung "
        "kamu panggil pada langkah berikutnya."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING",
                      "description": "Kata kunci, mis. 'decompile', "
                                     "'strings', 'ghidra', 'breakpoint'."},
        },
        "required": ["query"],
    },
}

_BRAIN_TOOL = {
    "name": "brain",
    "description": (
        "Pikirkan dan jawab. Kirim ucapan pengguna VERBATIM (kata demi kata, "
        "tanpa diubah, tanpa diterjemahkan) ke otak kustom, lalu ucapkan "
        "kembali jawabannya APA ADANYA — tanpa meringkas, menambah, atau "
        "mengarang. Wajib untuk perintah AKSI/KERJA (aturan A); perintah "
        "kecil dan obrolan ringan TIDAK lewat sini — tangani langsung."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "utterance": {"type": "STRING",
                          "description": "Ucapan pengguna, verbatim."},
        },
        "required": ["utterance"],
    },
}

# ── two-tier routing (live mode) ────────────────────────────────────────────
#
# Read / look / open / search / quick commands stay on the Live model: it is
# fast and these are the turns where speed is felt. Anything that CREATES or
# EDITS long text — a web page, code, a document, a deep analysis — goes to
# the custom brain, which is slower but actually follows instructions. One
# extra tool is what makes that split possible without taking any capability
# away from the fast path.
_CUSTOM_BRAIN_TOOL = {
    "name": "custom_brain",
    "description": (
        "Otak pintar untuk tugas BERAT yang butuh menulis atau mengedit: "
        "membuat halaman web/HTML, menulis atau mengubah kode, menyusun "
        "dokumen panjang, menganalisis isi dokumen, atau tugas bertahap yang "
        "rumit. Kirim permintaan pengguna apa adanya (verbatim). "
        "JANGAN pakai untuk membaca, melihat, membuka, mencari, mengirim "
        "pesan, atau perintah cepat — untuk itu pakai tool biasa. Tool ini "
        "butuh beberapa detik, jadi hanya untuk pekerjaan yang memang berat."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "request": {"type": "STRING",
                        "description": "Permintaan pengguna, verbatim."},
        },
        "required": ["request"],
    },
}

_TWO_TIER_NOTE = (
    "[OTAK DUA TINGKAT]\n"
    "Kamu punya dua cara bekerja. Untuk MEMBACA, MELIHAT, MEMBUKA, MENCARI, "
    "mengirim pesan, atau perintah cepat: kerjakan sendiri dengan tool biasa — "
    "kamu cepat. Untuk MEMBUAT atau MENGEDIT sesuatu yang butuh menulis "
    "panjang (halaman web/HTML, kode, dokumen, analisis mendalam): panggil "
    "tool `custom_brain` dengan permintaan pengguna apa adanya, lalu sampaikan "
    "hasilnya. Dia lebih lambat beberapa detik tetapi jauh lebih pintar — "
    "jangan ragu memakainya untuk pekerjaan berat.\n"
)

# ── deterministic routing (does not depend on the model's judgement) ────────
#
# A tool description is a suggestion; the Live model ignored it and answered a
# "send an email" request by opening WhatsApp and searching for a name nobody
# said. So the split is enforced in CODE — see core/intent.py for the verb
# lists and the reasoning. Writing verbs (send / create / edit / change /
# delete / schedule …) send the turn to the custom brain; reading verbs
# (open / read / search / show / play) stay on the fast path.
from core.decide import (decide_turn, SMALL as DEC_SMALL,   # noqa: E402
                         MODE as DEC_MODE, OFFER_REPLY as DEC_OFFER,
                         FORCE_BRAIN as DEC_FORCE, BRAIN as DEC_BRAIN)
from core.intent import (is_write_intent, is_read_only, allows_gemini,   # noqa: E402
                         claims_done, _wants_recall, classify_turn)
from core.jobs import JobRunner                                    # noqa: E402
from core.model_switch import resolve as _resolve_model_choice     # noqa: E402
from core.agent import audit as _agent_audit                       # noqa: E402
# Keputusan izin kini lewat pintu pusat core/decision_loop.py (yang memanggil
# core/agent/permissions.py) — GrokBot 22 Okt.
from core import decision_loop as _decision                        # noqa: E402
from core import tool_trust                                        # noqa: E402
from core.silence import stay_silent_plan                          # noqa: E402
from core.agent.runner import (AgentRunner,                        # noqa: E402
                               clear_pending as _clear_pending_agent,
                               load_pending as _load_pending_agent)

# Actions heavy enough that blocking the session on them is the wrong trade:
# they belong on the job queue, where a reconnect cannot kill them and the
# microphone stays free.
_BACKGROUND_TOOLS = {"dev_agent", "code_helper", "document_review",
                     "meeting_notetaker"}

# Aksi yang mengirim sesuatu keluar: hasilnya kecil tapi waktunya tidak
# bisa dipastikan (server menerima permintaan, menunggu balasan). Selalu
# dijalankan di latar supaya pengguna tidak membisu menunggu, dan hasilnya
# diucapkan ketika memang kembali.
_LONG_TOOLS = {"send_message", "composio_actions", "cloud_agent",
               "telegram_remote"} | _BACKGROUND_TOOLS

# Fase 4 Slice 2 — bagian prompt yang menjelaskan siapa pemilik ucapan
# penerimaan pekerjaan berat. Disuntikkan ke [ACKNOWLEDGE BEFORE A SILENCE]
# sebagai token {long_tools} (lihat _build_config).
_LONG_TOOLS_NOTE = (
    "Pekerjaan berat (kirim email, pesan, tugas panjang) TIDAK mengikuti "
    "aturan di atas: aplikasi sudah mengucapkan [PEKERJAAN_DITERIMA] "
    "sendiri dan respons tool-nya sengaja sunyi — jangan mengucapkan "
    "penerimaan lagi, jangan menebak status, dan jangan mengarang bahwa "
    "hasil sudah ada. Hasil yang benar-benar mendarat datang sendiri "
    "kemudian; kalau pengguna bertanya sebelum itu, jawab jujur bahwa "
    "pekerjaan masih berjalan."
)

# Fase 4 Slice 5 — jendela sunyi. Di bawah ambang ini pengguna dianggap
# masih ada di percakapan: hasil kerja diucapkan begitu tiba. Setelah
# melewatinya, hasil keberhasilan ditahan (bicara ke ruangan kosong adalah
# spam) dan disampaikan satu blok [HASIL_TUNDA] pada giliran berikutnya.
QUIET_WINDOW_SECONDS = 30

# Fase 4 Slice 1 — giliran yang bukan untuk JARVIS (pengguna bicara ke orang
# lain). _brain_turn mengembalikan tag ini; pemanggil menerjemahkannya jadi
# respons tool sunyi, dan prompt menginstruksikan giliran diakhiri tanpa
# bicara. Tagnya sendiri tak pernah sampai ke model di jalur biasa.
_NOT_FOR_ME_TAG = "[TIDAK_ADA_JAWABAN]"

# Kata yang menunjukkan composio_actions hanya membaca — itu cepat, tidak
# perlu antrean.
_READ_WORDS = ("status", "list", "get", "find", "search", "read", "check",
               "refresh", "ambil", "lihat", "cari", "cek")


# Tool yang boleh dipegang Gemini di mode hibrida — hanya yang TIDAK butuh
# berpikir: buka aplikasi, putar video, cek jadwal/cuaca/status, cari cepat.
# Semua tool aksi (kirim, buat, edit, hapus, jalankan perintah, tulis dokumen)
# sengaja TIDAK ada di sini: itu tugas otak custom, dan memberi Gemini tool
# itu membuatnya mengerjakan sendiri tugas yang seharusnya diserahkan.
_VOICE_TOOLS = (
    "youtube_video", "video_player", "browser_control", "open_app",
    "weather_report", "calendar_assistant", "system_status", "web_search",
    "screen_process", "model_status", "computer_control",
    # 24 Okt — MEMORI. Tanpa dua tool ini mulut tidak punya cara mengingat
    # apa pun: di log, Gemini mencari "riwayat obrolan terakhir" ke Google dan
    # mencoba MENGETIK ke layar untuk menyimpan cerita pengguna. Keduanya
    # aman (lokal, tanpa efek keluar) dan justru yang paling dibutuhkan saat
    # mode curhat — di sana `brain` dimatikan, jadi memori harus lewat sini.
    "save_memory", "recall_memory",
)


def _voice_tool_decls(decls) -> list:
    """Deklarasi tool ringan untuk mulut (Gemini) di mode hibrida."""
    want = set(_VOICE_TOOLS)
    return [d for d in decls
            if isinstance(d, dict) and d.get("name") in want]


def _voice_interface_prompt(name: str) -> str:
    """The mouth's whole instruction set in hybrid mode.

    Deliberately tiny: the mouth does not think, so nothing here can go
    wrong. Identity stays because the name is spoken, not reasoned about."""
    return (
        f"[MODE ANTARMUKA SUARA]\n"
        f"Kamu adalah suara dan telinga {name}. Ada DUA jalur:\n\n"
        "A. AKSI / KERJA - kirim, buat, buatkan, tulis, edit, ubah, ganti, "
        "perbaiki, hapus, tambah, susun, rangkum, ringkas, rekap, rapikan, "
        "pindah, salin, unduh, pasang, ekspor, impor, terjemahkan, "
        "gabungkan, jalankan, kerjakan, lakukan, jadwalkan (versi Inggris: "
        "send, create, build, write, edit, fix, delete, save, update, add, "
        "remove, refactor, deploy, summarize, translate, convert, export, "
        "schedule) - atau permintaan yang hasilnya berupa pekerjaan: "
        "panggil `brain` dengan kata-kata pengguna PERSIS.\n"
        "B. PERINTAH KECIL / CEPAT - buka, tutup, lihat, tampilkan, putar, "
        "cek, cari, refresh, segarkan, muat ulang (open, show, play, check, "
        "search, refresh) - panggil tool yang sesuai LANGSUNG, jangan lewat "
        "`brain` (itu hanya membuat pengguna menunggu):\n"
        "   - buka/tutup aplikasi -> open_app\n"
        "   - putar video / youtube -> youtube_video atau video_player\n"
        "   - cek / lihat jadwal, kalender, agenda -> calendar_assistant\n"
        "   - cuaca -> weather_report\n"
        "   - status sistem / RAM / CPU -> system_status\n"
        "   - cari di web -> web_search\n"
        "   - refresh / muat ulang halaman -> browser_control\n"
        "C. OBROLAN / PERTANYAAN RINGAN - jawab sendiri, satu dua kalimat.\n"
        "D. MEMORI - satu-satunya cara kamu mengingat. Dua tool:\n"
        "   - pengguna bertanya apa yang kamu ingat / apa yang pernah ia "
        "ceritakan -> panggil recall_memory (query kosong = tampilkan semua).\n"
        "   - pengguna minta menyimpan sesuatu, atau bercerita hal pribadi "
        "yang layak diingat -> panggil save_memory.\n"
        "   - JANGAN mencari memori lewat web_search, browser_control, atau "
        "layar. Memori ada di recall_memory, bukan di internet.\n"
        "   - MODE CURHAT tidak mematikan dua tool ini. Yang dimatikan di mode "
        "curhat hanya `brain`; menyimpan dan mengingat tetap boleh.\n"
        "   - Kalau ada blok [KONTEKS CURHAT SEBELUMNYA], itu latar cerita "
        "lama: pakai untuk menyambung, jangan minta pengguna mengulang, dan "
        "jangan dibacakan.\n"
        "Ragu antara A dan B? Pilih A.\n\n"
        "Aturan lain:\n"
        "- JANGAN mengucapkan apa pun sebelum memanggil `brain`. Jawaban "
        "`brain` adalah satu-satunya yang terdengar.\n"
        "- Ucapkan jawaban `brain` kata demi kata, dalam bahasa jawaban itu "
        "tertulis. Jangan meringkas, menerjemahkan, menambah, atau "
        "memperbaiki.\n"
        "- KECUALI bila jawaban itu berbentuk INSTRUKSI untukmu: jalankan "
        "dengan kalimatmu sendiri dalam bahasa pengguna.\n"
        "- Kalau jawaban berisi pertanyaan, ucapkan lalu tunggu jawaban.\n"
        "- Teks di antara <<INSTRUKSI UNTUKMU>> dan <<SELESAI INSTRUKSI>> "
        "adalah perintah untukmu: JANGAN pernah membacakannya ke "
        "pengguna, dan jangan menyebut kata INSTRUKSI.\n"
        "- Jangan mengarang hasil: kalau tool gagal, katakan gagal.\n"
        "\n" + _LANGUAGE_NOTE + "\n"
    )

# RMS below which 16-bit PCM is treated as room silence; above _LEVEL_FULL it
# reads as a full-height waveform. Tuned so ordinary speech lands mid-range and
# the bars still move for a quiet talker — language- and device-independent.
_LEVEL_FLOOR = 60.0
_LEVEL_FULL  = 2600.0


def _pcm_level(samples) -> float:
    """Map a block of int16 PCM samples to a 0.0–1.0 loudness level for the HUD
    waveform. Returns 0.0 on empty/invalid input so it can never raise."""
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0
    if rms <= _LEVEL_FLOOR:
        return 0.0
    return min(1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))


# ── Viseme extraction ─────────────────────────────────────────────────────────
# The avatar's mouth used to be driven by one RMS value per ~200 ms write batch,
# which is five updates a second averaged over a fifth of a second — it could
# only ever flap. These read the *shape* of each 20 ms slice straight from the
# spectrum of the audio being played, so no transcript, no forced alignment and
# no language assumption: it works the same for Turkish and English.
#
# Two numbers come out. Openness tracks the first formant — F1 climbs as the jaw
# drops, so /a/ reads open and /i/ or /u/ read closed. Width tracks the second —
# F2 is high for spread vowels (/i/, /e/) and low for rounded ones (/u/, /o/).
# Extra time beyond the device's reported output latency before the microphone
# is trusted again: covers room decay and the speaker's own settling.
_TAIL_MARGIN = 0.25

_VIS_WIN = 1024        # ~43 ms analysis window at 24 kHz: enough for formants
_VIS_HOP = 480         # 20 ms between frames, i.e. 50 shapes a second

# Delay from handing the first bytes of a reply to an already-running output
# stream to hearing them: one callback period, plus whatever the DAC adds.
_FIRST_SOUND = CHUNK_SIZE / RECEIVE_SAMPLE_RATE      # ~43 ms
# How far past the device's own buffer the mouth's timeline may drift before it
# is re-anchored. The buffer is the hard limit on how much audio can be queued
# ahead, so anything beyond it plus a margin for clock error is impossible.
_CURSOR_SLACK = 0.15

# Erring early is the safe direction. A viewer tolerates a mouth that moves
# slightly before the sound far better than one that moves after it — the
# broadcast limits are about 45 ms of lag against 125 ms of lead — so where
# this is uncertain it is biased to lead.


def _pcm_visemes(samples, sr: int = 24000):
    """Slice a PCM block into (level, openness, width) frames, one per 20 ms.

    Returns [] on anything unexpected — the mouth falls back to loudness-only
    articulation rather than the caller having to handle an error.
    """
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size < _VIS_WIN:
            return []
        win = np.hanning(_VIS_WIN).astype(np.float32)
        freqs = np.fft.rfftfreq(_VIS_WIN, 1.0 / sr)
        b_f1_lo = (freqs >= 150) & (freqs < 450)     # F1 of close vowels
        b_f1_hi = (freqs >= 450) & (freqs < 1100)    # F1 of open vowels
        b_f2_bk = (freqs >= 600) & (freqs < 1300)    # F2 of rounded vowels
        b_f2_fr = (freqs >= 1700) & (freqs < 3200)   # F2 of spread vowels
        b_hiss = (freqs >= 3800) & (freqs < 8000)    # fricatives

        # One frame per hop across the *whole* block. Stepping only while a full
        # window fits stopped 1024 - 480 samples short of the end, so a 200 ms
        # batch yielded 160 ms of schedule: the mouth ran out of frames before
        # the audio ran out of sound, and each batch no longer lined up with the
        # end of the one before it. Losing 20 % of every batch is most of why
        # the mouth did not track the words.
        out = []
        for start in range(0, x.size, _VIS_HOP):
            # The level gates closures, so it is measured over exactly this
            # 20 ms and never looks ahead. The spectrum needs a longer window
            # to resolve formants and may be short-filled at the very end.
            level = _pcm_level(x[start:start + _VIS_HOP])
            seg = x[start:start + _VIS_WIN]
            if seg.size < _VIS_WIN:
                seg = np.concatenate([seg, np.zeros(_VIS_WIN - seg.size,
                                                    dtype=np.float32)])
            if level <= 0.0:
                out.append((0.0, 0.0, 0.0))
                continue
            mag = np.abs(np.fft.rfft((seg - seg.mean()) * win))
            f1l, f1h = float(mag[b_f1_lo].sum()), float(mag[b_f1_hi].sum())
            f2b, f2f = float(mag[b_f2_bk].sum()), float(mag[b_f2_fr].sum())
            hiss = float(mag[b_hiss].sum())

            openness = f1h / (f1l + f1h + 1e-6)
            width = (f2f - f2b) / (f2f + f2b + 1e-6)
            # A wide-open jaw physically cannot purse, so openness damps width.
            # /a/ has a low enough F2 to read as "rounded" on the bands alone;
            # letting openness suppress the width term is what keeps an open
            # vowel from pursing.
            width *= (1.0 - openness) ** 0.8
            # Fricatives are formed with a nearly closed mouth.
            h = hiss / (f1l + f1h + f2b + f2f + hiss + 1e-6)
            openness *= 1.0 - 0.65 * min(1.0, h * 2.5)
            out.append((level,
                        float(min(1.0, max(0.0, openness))),
                        float(min(1.0, max(-1.0, width)))))
        return out
    except Exception:
        return []


def _describe_tools(declarations) -> str:
    """One line per capability, straight from the live tool declarations.

    Derived rather than written down: the action and plugin registries are
    discovered at startup, so whatever the user has installed is what the model
    is told it can do. Adding a plugin extends this by itself, and removing one
    stops the model from claiming an ability it no longer has.
    """
    lines = []
    for d in declarations or ():
        try:
            name = d.get("name") if isinstance(d, dict) else getattr(d, "name", None)
            desc = (d.get("description") if isinstance(d, dict)
                    else getattr(d, "description", "")) or ""
        except Exception:
            continue
        if not name:
            continue
        desc = " ".join(str(desc).split())
        lines.append(f"- {name}: {desc[:150]}" if desc else f"- {name}")
    return "\n".join(lines)


def _describe_limits(has_vision: bool, has_mic: bool) -> str:
    """The other half of self-knowledge: what is out of reach, and why.

    Derived from how the program is actually built, not from a list of refusals.
    A model that knows its boundaries stops improvising around them, and stating
    them as architecture rather than as rules keeps the answer honest in any
    language.
    """
    out = [
        "- Anything not listed above is outside your reach. Say so in one clause "
        "and offer the nearest thing you can actually do — never mime an action "
        "you cannot take, and never report a result you did not get.",
        "- You act on this machine only. You cannot reach the user's other "
        "devices, accounts or hardware except through the tools listed above.",
        "- You remember what is in the memory block and what has been said this "
        "session. Anything else you were told before is gone unless it was saved.",
    ]
    if has_vision:
        out.append(
            "- Your sight is not continuous. You see nothing until you call a "
            "vision tool, and then only that single frame at that moment — you "
            "cannot watch, monitor or notice something changing on screen.")
    else:
        out.append("- You have no sight at all in this build.")
    if has_mic:
        out.append(
            "- You hear nothing while the microphone is muted, and you cannot "
            "unmute it yourself.")
    return "\n".join(out)


_PROJECT_WORDS = ("proyek", "project", "kerjaan", "pekerjaan", "folder kerja",
                  "revisi", "aplikasi", "lanjutkan", "bangun", "buatkan")


def _memory_for_prompt(memory, utterance: str = "") -> str:
    """Memori untuk system prompt — dengan satu aturan: agenda basi tidak ikut.

    `projects.current_project` adalah catatan tugas TERAKHIR, bukan hal yang
    sedang dibicarakan. Kalau ikut di setiap giliran, model mengangkatnya
    sendiri ("bagaimana proyek Hermes Anda?") — persis keluhan pengguna.
    Karena itu kategori itu hanya disertakan bila pengguna memang membicarakan
    proyek.
    """
    try:
        mem = dict(memory or {})
    except Exception:
        return format_memory_for_prompt(memory)
    said = (utterance or "").lower()
    if not any(w in said for w in _PROJECT_WORDS):
        mem.pop("projects", None)
    return format_memory_for_prompt(mem)


def _render_prompt(template: str, values: dict) -> str:
    """Fill {tokens} in the prompt template.

    A plain replace rather than str.format: the file is meant to be edited by
    hand, and a stray brace in someone's own wording must never take the app
    down at startup.
    """
    out = template or ""
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

# Transcript chunks shorter than this may legitimately repeat ("evet, evet"),
# so only longer ones are treated as duplicates.
_REPEAT_MIN = 12


def _is_repeat_chunk(txt: str, buf: list) -> bool:
    """True if this transcript chunk has already been seen this turn.

    Guards against the API re-sending the tail of a response across the several
    turn_completes a tool-using turn produces.
    """
    if len(txt) < _REPEAT_MIN:
        return bool(buf) and txt == buf[-1]
    joined = " ".join(buf)
    return txt in joined

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

from core.tool_decls import TOOL_DECLARATIONS   # noqa: E402  (dipindah dari blok inline; isinya identik)

class _ReconnectSignal(Exception):
    """Raised inside the session TaskGroup to force a clean, voluntary reconnect
    (e.g. the user picked a new voice — the voice is fixed at connect time, so
    the session must be rebuilt).

    Carries `keep_context`: True for an ordinary rebuild, where the stored
    resumption handle is replayed and the conversation continues; False when the
    new session must genuinely start clean (see the voice-change note in
    _on_voice_change)."""

    def __init__(self, keep_context: bool = True):
        super().__init__()
        self.keep_context = keep_context


def _is_reconnect_signal(exc: BaseException) -> bool:
    """True if `exc` is a _ReconnectSignal, or a(n) (Base)ExceptionGroup that
    wraps one — TaskGroup bundles child exceptions into a group."""
    if isinstance(exc, _ReconnectSignal):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_reconnect_signal(sub) for sub in exc.exceptions)
    return False


# The Live API ends a session when it reaches its duration limit: the server
# sends GoAway and, if the client is still receiving, aborts the socket with
# 1008. That is the API working as designed — the answer is to reconnect at
# once with the resumption handle, not to treat it as a failure. The message
# arrives wrapped in whatever group the TaskGroup built around it, so the
# check walks the tree the same way _is_reconnect_signal does.
_GOAWAY_NEEDLES = ("1008", "goaway", "session duration")


def _is_session_limit(exc: BaseException) -> bool:
    """True when the exception is the API's session-duration limit, not a
    fault: a 1008 abort after GoAway, possibly nested in an ExceptionGroup."""
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_session_limit(sub) for sub in exc.exceptions)
    text = str(exc).lower()
    return any(n in text for n in _GOAWAY_NEEDLES)


def _keep_context_of(exc: BaseException) -> bool:
    """Read `keep_context` off a reconnect signal, unwrapping the group the
    TaskGroup put it in. Defaults to True: an unexpected shape must not silently
    wipe the conversation."""
    if isinstance(exc, _ReconnectSignal):
        return getattr(exc, "keep_context", True)
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            if _is_reconnect_signal(sub):
                return _keep_context_of(sub)
    return True


def _composio_not_done(text: str) -> bool:
    """True kalau hasil composio_actions sebenarnya TIDAK melakukan apa pun.

    Plugin itu melaporkan setiap kegagalan sebagai kalimat Indonesia, bukan
    exception — jalur suara membutuhkan itu. Konsekuensinya sebuah job yang
    "selesai" bisa saja email yang tidak pernah terkirim. Pemilik kalimat
    itu adalah plugin, jadi pengenalnya juga diambil dari plugin — bukan
    daftar karangan di sini yang bisa melenceng saat kalimatnya berubah."""
    try:
        from plugins import composio_actions as _ca
        return _ca.looks_not_done(text)
    except Exception:
        return False


def _job_verdict(job) -> tuple[str, str]:
    """('done' | 'failed' | 'waiting', ringkasan) — apa yang SEBENARNYA terjadi.

    Status job saja tidak cukup: composio_actions melaporkan kegagalan
    sebagai kalimat, jadi job yang 'done' bisa saja email yang tidak
    terkirim. Mengumumkan "sudah selesai" untuk kegagalan adalah keluhan
    bos — pengguna harus bertanya dulu apakah emailnya terkirim."""
    ok = job.status == "done"
    text = " ".join((job.result or "").split())
    summary = (text.splitlines() or [""])[0][:280]
    if summary.upper().startswith("KONFIRMASI DIPERLUKAN"):
        return "waiting", summary
    if not ok or (getattr(job, "tool", "") == "composio_actions"
                  and _composio_not_done(text)):
        return "failed", summary
    return "done", summary


class JarvisLive:
    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self._asst_name     = "JARVIS"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._live_down           = False   # True saat socket Live tertutup
        self._mouth_pending       = None    # (sejak, jawaban) menunggu suara
        self._awaiting_reply      = False   # menunggu suara pertama mulut
        self._session_started     = 0.0     # waktu sesi Live ini dibuka
        self._rotating            = False   # sedang memutar sesi (preventif)
        self._loop                     = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        # Transcript-driven mouth shapes for the avatar. Fed from the receive
        # loop as words arrive, drained by the playback loop against the audio.
        self._visemes              = VisemeStream()
        self._last_out_logged      = ""      # de-dupes a re-sent transcript tail
        # Push-to-talk
        self._ptt_enabled          = False
        self._ptt_held             = False
        self._ptt                  = None    # core.hotkey.PushToTalk
        self._out_level            = 0.0     # level of the audio being played right now
        self._echo                 = EchoGuard()
        # `stream.write()` returns when the buffer accepts the audio, not when the
        # speaker has finished with it, so sound is still in the room after the
        # speaking flag drops. Streaming the microphone during that gap is how an
        # assistant ends up answering itself. Measured from the device rather than
        # guessed; see _play_audio.
        self._out_latency          = 0.20    # seconds, replaced with the real value
        self._tail_until           = 0.0     # monotonic time the echo tail expires
        # Wall-clock time at which the audio written next will begin to sound.
        # The mouth is scheduled against this, never against "now": batches are
        # handed to the device far faster than they play, so "now" ran the lips
        # ahead of the words and cut every schedule short. 0 = nothing playing.
        self._play_cursor          = 0.0
        self.ui.on_push_to_talk   = self.set_push_to_talk
        self.ui.ptt_hold          = self._on_ptt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_voice_change   = self._on_voice_change     # voice picker → rebuild session
        self.ui.on_audio_device_change = self._on_audio_device_change
        # Which brain answered each heavy turn — console + HUD log panel. A
        # provider rung is otherwise indistinguishable from a Gemini turn, and
        # "is my external model actually being used?" deserves an answer.
        _gemini.set_brain_log_hook(self.ui.write_log)
        self._reconnect_event: asyncio.Event | None = None
        self._reconnect_keep = True   # False → next rebuild drops the resumption handle
        # Hybrid engine: voice_brain="custom" moves the thinking to the user's
        # own provider; the Live session then only listens and speaks.
        self._brain_mode    = False
        self._brain         = None    # core.custom_brain.CustomBrain or None
        self._brain_system  = ""
        # The words of the utterance being handled right now, and whether a
        # brain turn is already running (so the router cannot re-route the
        # brain's own tool calls into the brain again).
        self._live_utterance = ""
        self._utt_open       = False
        self._in_brain       = False
        # Mode paksa (perintah suara "alihkan ke custom"): semua pekerjaan
        # lewat otak custom, termasuk perintah ringan. Per sesi saja —
        # sengaja TIDAK ditulis ke config supaya tidak jadi jebakan permanen.
        self._force_brain    = False
        # Tawaran mouse/keyboard: aksi berat ditawarkan dulu, bukan
        # langsung dijalankan maupun langsung dialihkan.
        self._vent_mode      = False   # mode curhat: Gemini penuh
        self._vent_handled   = ""      # penjaga anti-ulang
        # Abort beruntun (24 Okt): hitung sesi yang hidup < 20 detik supaya
        # reconnect tidak berputar tanpa jeda.
        self._fast_aborts    = 0
        self._last_attempt_at = 0.0
        self._switch_handled = ""
        self._cc_offer       = None
        self._cc_approved    = False   # izin sekali pakai setelah bos memilih Gemini
        self._turn_tool_used = False   # did any tool run for this utterance?
        # Background work: survives reconnects, speaks its own result.
        self._jobs: JobRunner | None = None
        self._pending_job_msgs: list[str] = []
        # Fase 4 Slice 2: fakta hasil pekerjaan latar yang sudah mendarat —
        # cincin kecil di RAM, disuntikkan ke prompt saat menyambung supaya
        # "tadi gimana?" dijawab dari kenyataan, bukan tebakan.
        self._landed = LandedRing()
        # Tanda terima yang menunggu diucapkan SETELAH respons tool terkirim
        # (lihat _flush_receipts — urutan ini yang menjaga sesi tetap hidup).
        self._receipt_pending: list[str] = []
        # Fase 4 barge-in: catatan sekali pakai berisi apa yang terpotong
        # saat pengguna memotong (ekor ucapan JARVIS + ucapan pemotong).
        # Disuntik sekali, saat ucapan berikutnya mulai, lalu dibuang.
        self._cut_note = ""
        # Fase 4 Slice 5: hasil "done" yang mendarat saat pengguna sudah
        # diam ≥30 s — ditahan di sini, disampaikan sekali sebagai satu
        # blok [HASIL_TUNDA] pada giliran berikutnya. Kegagalan & permintaan
        # konfirmasi TIDAK pernah masuk sini.
        self._deferred_results: list[str] = []
        # Fase 4 Kandidat C: stempel waktu ucapan JARVIS terakhir. "Sudah
        # bicara" berarti berbicara SETELAH ucapan terakhir pengguna — tak
        # ada siklus yang perlu direset, ucapan pengguna berikutnya sendiri
        # yang menutup siklus ini (stempel jadi usang). 0.0 = belum pernah
        # bicara sejak aplikasi mulai.
        self._last_spoke: float = 0.0
        # Tools the user has switched off (config: tool_permissions.deny).
        self._deny_tools: set[str] = set()
        self._perm_cfg: dict = {}          # full config for the policy engine
        self._pending_confirm: dict | None = None
        self._pending_agent_state: dict | None = None
        # Share-screen coaching: Jarvis watches the screen and guides.
        self._screen_on = False
        self._screen_goal = ""
        self._screen_hint_last = ""
        # Latest frame shared between the fast preview lane and the slow
        # advice lane — so the vision call never blocks the picture.
        self._screen_last_png = b""
        self._screen_last_jpeg = b""
        self._screen_last_size = (0, 0)
        self._screen_err = 0

        # ── Session resumption ─────────────────────────────────────────
        # The server issues a resumption handle every few seconds and reissues
        # it as the conversation moves on. Before this, session_resumption was
        # switched ON in the config and the update was never read, so the handle
        # was thrown away and EVERY reconnect — a dropped packet, a voice change,
        # switching microphone — started an empty session. "Unlimited sessions"
        # leaked through exactly this hole.
        #
        # Deliberately in RAM only, never written to disk. Persisting it would
        # make a fresh launch continue yesterday's conversation, which sounds
        # appealing but breaks the session-summary flow: _save_session_summary
        # runs at shutdown and the morning briefing pops it the next day. A
        # conversation that never ends never produces a summary, and the
        # "yesterday we talked about…" line silently disappears.
        self._resume_handle: str | None = None
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._ever_connected   = False          # True once any Live session has opened
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._session_log: list[str] = []          # conversation turns for end-of-session summary
        # Audit 25 Okt (M-5): fire-and-forget asyncio.create_task bisa di-GC
        # sebelum selesai bila tak ada referensi kuat (peringatan resmi asyncio).
        # Task latar disimpan di sini; done-callback membuang entrinya.
        self._bg_tasks: set = set()

        self._enhanced_live = True  # proactive audio; auto-disabled if the server rejects it
        self._tuned_live    = True  # turn-taking / media / thinking knobs; same fallback

        _base_dir = Path(__file__).resolve().parent
        _inline_names = {t["name"] for t in TOOL_DECLARATIONS}

        # File-backed tools: every actions/*.py with a TOOL dict, discovered the
        # same way plugins are. Reserved names = the inline tools above, so an
        # action can never shadow one.
        self._action_registry = discover_actions(
            actions_dir=_base_dir / "actions",
            reserved_names=_inline_names,
            logger=lambda msg: print(f"[Actions] {msg}"),
        )

        # Plugins must not collide with either an inline tool or a discovered action.
        _core_names = _inline_names | self._action_registry.names()
        self._core_names = _core_names          # dipakai saat reload plugin
        self._plugin_registry = discover_plugins(
            plugins_dir=_base_dir / "plugins",
            core_tool_names=_core_names,
            # Console gets the full boot transcript; the activity log gets only
            # what the user has to know about. Every plugin loading correctly is
            # the expected case and does not belong in their conversation.
            logger=lambda msg: print(f"[Plugins] {msg}"),
            notify=lambda msg: self.ui.write_log(f"SYS: {msg}"),
        )
        self.ui.get_plugins = self._plugin_registry.list_for_ui
        # The TOOLS panel lists actions too — the same declarations the model
        # receives, so the list cannot drift from what is actually callable.
        self.ui.get_actions = lambda: [
            {"name": d.get("name", "?")}
            for d in self._action_registry.get_tool_declarations()]
        self.ui.get_plugin_settings = self._plugin_registry.settings_schemas  # ⚙ settings tab
        self.ui.request_say = self.plugin_say   # plugins: mid-task speech channel

        # ── MCP servers (external tool servers, e.g. REA) ─────────────────────
        # Booted in the background with a bounded wait: an MCP server (npx cold
        # start, Ghidra bridges) must never delay the mic. Tools that miss the
        # deadline join the declarations on the next session.
        self._mcp = MCPManager(
            project_root=_base_dir,
            logger=lambda msg: print(f"[MCP] {msg}"),
            notify=lambda msg: self.ui.write_log(msg),
        )
        self._mcp.start_all(deadline_s=30.0)
        import atexit as _atexit
        _atexit.register(self._mcp.stop_all)
        # CONNECTIONS panel: list, add/edit and test MCP servers without anyone
        # opening config/mcp.json. The panel asks for specs and calls back here.
        self.ui.get_mcp_servers    = self._mcp.servers_for_ui
        self.ui.save_mcp_server    = self._mcp.save_server
        self.ui.delete_mcp_server  = self._mcp.delete_server
        self.ui.restart_mcp_server = self._mcp.restart_server

        # ── Voice-macro store ────────────────────────────────────────────────
        # Taught shortcuts ("when I say X, do Y"). main.py consults this store
        # BEFORE the model for typed and remote commands, so a typed trigger is
        # exact and instant; spoken triggers arrive through the plugin instead.
        # See core/macros.py for why the store is core and not plugin-owned.
        _macros.bind(action_registry=self._action_registry,
                     plugin_registry=self._plugin_registry,
                     player=self.ui, log=self.ui.write_log)

        # ── Wake word ────────────────────────────────────────────────────────
        # _awake gates the mic (see _listen_audio) and the background speakers.
        # A fresh launch starts AWAKE on purpose: the user asked to never have
        # to say "Hey Jarvis" just to open the app. The wake word still earns
        # its keep after that — after WAKE_SLEEP_TIMEOUT of silence JARVIS
        # sleeps and the wake word (or the UI wake button) brings it back.
        self._wake_enabled     = get_wake_word_enabled()
        self._awake            = True
        self._wake_detector: WakeWordDetector | None = None
        self._wake_sleep_timeout = WAKE_SLEEP_TIMEOUT
        # Last few seconds heard while asleep. When the wake word fires, the
        # sentence that came WITH it ("Hey Jarvis, what's the weather") was
        # never streamed — without this buffer JARVIS wakes up having heard
        # nothing, and "tidak ada jawaban" follows. See wake().
        self._sleep_buf: list = []
        self._SLEEP_BUF_FRAMES = 48      # ~3 s at 64 ms per mic block
        self._wake_warn_at = 0.0         # rate-limit for the silent-deaf warning

        # Restore the saved push-to-talk preference. Doing it here rather than
        # in __init__ means the hotkey thread only exists once there is a
        # session to talk to.
        if get_push_to_talk_enabled():
            try:
                self.set_push_to_talk(True)
            except Exception as e:
                print(f"[JARVIS] ⚠ Push-to-talk unavailable: {e}")
        # UI control surface for the Wake Word settings section.
        self.ui.wake_is_ready    = wake_is_ready          # () -> bool
        self.ui.wake_get_state   = self._wake_state       # () -> dict
        self.ui.on_wake_toggle   = self._ui_wake_toggle   # (enable: bool) -> str
        self.ui.on_wake_manual   = self._ui_wake_manual   # () -> toggle awake/asleep
        self.ui.on_wake_install  = self._ui_wake_install  # () -> (ok, msg)

    # ── Wake word: state machine ─────────────────────────────────────────────

    def _wake_state(self) -> dict:
        # A loaded, running detector is definitively ready; otherwise fall back
        # to the cheap on-disk model-file check (no Model construction).
        ready = bool(self._wake_detector and self._wake_detector.ready) or wake_is_ready()
        return {"enabled": self._wake_enabled, "awake": self._awake, "ready": ready}

    def _ensure_wake_detector(self) -> bool:
        """Load the detector once (model loads on first start). Idempotent."""
        if self._wake_detector is None:
            self._wake_detector = WakeWordDetector(
                on_detect=self._on_wake_detected,
                threshold=get_wake_threshold(),
                logger=lambda m: print(f"[Wake] {m}"),
                notify=lambda m: self.ui.write_log(f"SYS: {m}"),
            )
        if not self._wake_detector.ready:
            return self._wake_detector.start()
        return True

    def _on_wake_detected(self) -> None:
        """Called from the detector thread when 'Hey Jarvis' is heard."""
        self.wake(reason="wake word")

    def _warn_wake_deaf(self) -> None:
        """The wake word is on but the detector is not running — say so,
        rate-limited, instead of sitting silently deaf forever."""
        now = time.monotonic()
        if now - self._wake_warn_at > 30.0:
            self._wake_warn_at = now
            print("[Wake] detector not running — wake word cannot hear you")
            self.ui.write_log(
                "SYS: Wake detector not running — use WAKE NOW or re-enable "
                "the wake word in CONTROLS.")

    def wake(self, reason: str = "wake word") -> None:
        if self._awake:
            return
        self._awake = True
        self._last_user_speech = time.monotonic()   # start the auto-sleep clock now
        # Carry whatever was said while asleep into the session: those frames
        # were fed to the detector, never streamed, so "Hey Jarvis, <command>"
        # would otherwise wake JARVIS with the command lost. JARVIS is not
        # speaking while asleep, so there is no echo to guard against here.
        buf = getattr(self, "_sleep_buf", None)
        if buf:
            payload = b"".join(f.tobytes() for f in buf if f is not None)
            buf.clear()
            if payload:
                loop = getattr(self, "_loop", None)
                out_q = getattr(self, "out_queue", None)
                if loop is not None and out_q is not None:
                    loop.call_soon_threadsafe(
                        _put_drop_oldest,
                        out_q,
                        {"data": payload, "mime_type": "audio/pcm"},
                    )
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        self.ui.write_log(f"SYS: Awake — {reason}.")

    def sleep(self, reason: str = "timeout") -> None:
        if not self._awake:
            return
        self._awake = False
        self.set_speaking(False)
        self.ui.set_state("SLEEPING")
        self.ui.write_log(f"SYS: Sleeping — {reason}. Say 'Hey Jarvis' to wake me.")

    async def _run_sleep_watch(self) -> None:
        """Auto-sleep after the configured silence window (wake-word mode only)."""
        while True:
            await asyncio.sleep(5)
            if not self._wake_enabled or not self._awake:
                continue
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue
            # Never fall asleep while a background job is running: the user
            # is waiting for a result, not for the assistant to disappear.
            if self._jobs is not None and self._jobs.busy():
                self._last_user_speech = time.monotonic()
                continue
            if (time.monotonic() - self._last_user_speech) > self._wake_sleep_timeout:
                self.sleep(reason=(
                    f"no speech for {int(self._wake_sleep_timeout // 60)} minutes"))

    # ── Wake word: UI callbacks (called from the Qt thread) ──────────────────

    def _ui_wake_toggle(self, enable: bool) -> str:
        """Enable/disable wake word from the settings UI. Returns a status token:
        'enabled' | 'disabled' | 'need_download'."""
        if enable:
            if not wake_is_ready():
                return "need_download"
            self._wake_enabled = True
            save_wake_word_enabled(True)
            self._ensure_wake_detector()
            self.sleep(reason="wake word enabled")
            return "enabled"
        else:
            self._wake_enabled = False
            save_wake_word_enabled(False)
            self.wake(reason="wake word disabled")
            return "disabled"

    def _ui_wake_manual(self) -> None:
        """Manual sleep/wake button in the UI."""
        if not self._wake_enabled:
            return
        if self._awake:
            self.sleep(reason="you tapped sleep")
        else:
            self.wake(reason="you tapped wake")

    def _ui_wake_install(self) -> tuple[bool, str]:
        """Download openwakeword + the model (runs in a UI worker thread)."""
        # Triggered by the user pressing the button, so its progress is exactly
        # what they are waiting to see.
        return wake_install(logger=lambda m: print(f"[Wake] {m}"),
                            notify=lambda m: self.ui.write_log(f"SYS: {m}"))

    def plugin_say(self, instruction: str) -> None:
        """
        Thread-safe speech channel for plugins: lets a plugin ask JARVIS to
        say something short WHILE its run() is still executing (plugins block
        their executor thread, so they can't speak through the tool response
        until they finish). The instruction is injected into the Live session
        exactly like a proactive check-in; Gemini phrases it naturally in the
        user's language. Silently a no-op when no session is connected.
        """
        loop = getattr(self, "_loop", None)
        if not loop or not self.session:
            return

        async def _say():
            try:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": instruction}]},
                    turn_complete=True,
                )
                self._mark_spoke()
            except Exception as e:
                print(f"[PluginSay] {e}")

        try:
            asyncio.run_coroutine_threadsafe(_say(), loop)
        except Exception as e:
            print(f"[PluginSay] {e}")

    def request_reconnect(self, keep_context: bool = True, reason: str = ""):
        """Thread-safe: ask the run loop to tear down and rebuild the Live
        session. Called from the Qt thread. No-op until the async loop and
        reconnect event exist.

        `keep_context=False` drops the resumption handle so the new session
        starts empty — only for changes the server cannot apply to a resumed
        session."""
        loop = getattr(self, "_loop", None)
        ev   = self._reconnect_event
        self._reconnect_keep   = keep_context
        self._reconnect_reason = reason
        if loop and ev is not None:
            loop.call_soon_threadsafe(ev.set)

    def _on_voice_change(self):
        """Voice picker applied.

        The voice is baked into the session at connect time, so a rebuild is
        required. It is rebuilt WITHOUT the resumption handle on purpose:
        resuming restores the server's own session state, and the safe reading
        is that it restores the voice with it — which would make the picker
        appear to do nothing. Losing context here is acceptable because changing
        voice is a deliberate, rare act; losing it on a dropped packet was not."""
        self.request_reconnect(keep_context=False, reason="new voice")

    def _on_audio_device_change(self):
        """Microphone or speaker changed. Both streams are opened inside the
        session TaskGroup, so they can only be re-opened by rebuilding it —
        but the conversation is kept, which is the whole reason resumption
        landed before this feature did."""
        self.request_reconnect(keep_context=True, reason="audio device")

    async def _watch_reconnect(self):
        """Session-scoped task: when a voluntary reconnect is requested, raise a
        signal that unwinds the TaskGroup so the run loop rebuilds the session."""
        assert self._reconnect_event is not None
        await self._reconnect_event.wait()
        self._reconnect_event.clear()
        keep   = self._reconnect_keep
        reason = getattr(self, "_reconnect_reason", "") or "settings"
        self.ui.write_log(
            f"SYS: Applying {reason} — reconnecting"
            + ("..." if keep else " (starting a fresh conversation)...")
        )
        raise _ReconnectSignal(keep_context=keep)

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        # Respect wake-word sleep: a typed command must not be answered while
        # asleep either (the sleep gate is not just for the mic). Wake first with
        # "Hey Jarvis" or the WAKE NOW button.
        if self._wake_enabled and not self._awake:
            self.ui.write_log("SYS: I'm asleep — say 'Hey Jarvis' or tap WAKE NOW first.")
            return
        # Typed text (HUD box, Telegram remote) reaches the model the same way
        # a spoken sentence does — so it must reach the ROUTER the same way
        # too. Without this the router saw a stale utterance and the fast
        # model was free to improvise on a "kirim email" typed from a phone.
        self._live_utterance = str(text or "").strip()
        self._utt_open = False
        self._turn_tool_used = False
        # A taught shortcut is checked before the model sees the text: exact,
        # instant, and it keeps working with the plugin switched off. No match
        # (the normal case) falls through to the model unchanged.
        _macro_hit = _macros.try_trigger(text)
        if _macro_hit:
            self.ui.write_log(f"SYS: {_macro_hit}")
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _tail_active(self) -> bool:
        """True while the speakers may still be finishing our last sentence."""
        return time.monotonic() < self._tail_until

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self._tail_until = 0.0
            self._mark_spoke()   # Kandidat C — suara model keluar: sudah bicara
        else:
            # Hold the guard open across the device's own output latency plus a
            # margin for the room. The microphone is NOT muted during it — the
            # guard still lets a genuine reply through, so answering instantly
            # still works. Only our own echo is dropped.
            self._tail_until = time.monotonic() + self._out_latency + _TAIL_MARGIN
        if not value:
            # The echo history is deliberately NOT cleared here: the tail above
            # still needs it to recognise our own voice. It is dropped when the
            # tail expires. What the guard learned about the room always stays.
            self._out_level = 0.0
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def set_push_to_talk(self, enabled: bool) -> str:
        """Turn hold-to-talk on or off. Returns the scope actually achieved."""
        from core.hotkey import PushToTalk

        self._ptt_enabled = bool(enabled)
        self._ptt_held = False
        if not enabled:
            if self._ptt is not None:
                self._ptt.stop()
                self._ptt = None
            return "off"

        if self._ptt is None:
            self._ptt = PushToTalk(self._on_ptt)
        scope = self._ptt.start()
        # A window-scoped chord is a real limitation, not a detail — say it once
        # in the log so nobody wonders why it does nothing while another app is
        # focused. Reporting it must never be able to undo the thing it reports.
        try:
            self.ui.write_log(
                f"SYS: Push-to-talk on — hold {self._ptt.label}"
                + ("." if scope == "global"
                   else " (works while this window is focused)."))
        except Exception:
            pass
        return scope

    def _on_ptt(self, held: bool) -> None:
        """Chord pressed or released — may arrive on the hotkey thread."""
        self._ptt_held = held
        if held:
            # Holding the key is also a way to wake it, so push-to-talk works
            # without having to say the wake word first.
            if self._wake_enabled and not self._awake:
                self._awake = True
                self._last_user_speech = time.monotonic()
        try:
            self.ui.set_state("LISTENING" if held else "SLEEPING")
        except Exception:
            pass

    def interrupt(self) -> None:
        """Stop JARVIS mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[JARVIS] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        # The words we were about to mouth are never going to be spoken now.
        self._visemes.reset()
        self._play_cursor = 0.0     # next batch starts a fresh timeline
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "JARVIS").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "JARVIS"
            _user_name = ""

        memory     = load_memory()
        mem_str    = _memory_for_prompt(memory, self._live_utterance)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        # Address form is a property of the language being spoken, so it is
        # stated as a principle rather than a two-language lookup — the model
        # already knows the respectful register of whatever language it is in.
        _addr = (f"ADDRESS: Always call the user '{_user_name}'."
                 if _user_name
                 else 'ADDRESS: Address the user with the ordinary respectful form '
                      'for a superior in the language you are currently speaking — '
                      '"sir" in English, its everyday equivalent in any other '
                      'language. Never an archaic or aristocratic form, and never '
                      'the form from a different language than the one you are '
                      'speaking in this sentence.')
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        # Everything the model is told about *itself* is derived here, not
        # written into prompt.txt: the name comes from config, the platform from
        # the host, the capability list from the registries that were just
        # discovered. Rename the assistant, add a plugin or move to another OS
        # and this follows without anyone editing a prompt.
        _all_decls = (TOOL_DECLARATIONS
                      + self._action_registry.get_tool_declarations()
                      + self._plugin_registry.get_tool_declarations()
                      + (self._mcp.get_tool_declarations()
                         if self._read_brain_flag("mcp_voice_tools", False)
                         else []))
        _names = {(d.get("name") if isinstance(d, dict) else getattr(d, "name", ""))
                  for d in _all_decls}
        sys_prompt = _render_prompt(sys_prompt, {
            "assistant_name": self._asst_name,
            "platform": f"{_platform.system()} {_platform.release()}".strip(),
            "capabilities": _describe_tools(_all_decls),
            "limits": _describe_limits(
                has_vision="screen_process" in _names,
                has_mic=True,
            ),
            "long_tools": _LONG_TOOLS_NOTE,
        })

        # 24 Okt — URUTAN PENTING UNTUK CACHE. Provider (OpenAI/Anthropic)
        # menyimpan cache berdasarkan PREFIX yang identik. Dulu `time_ctx`
        # (jam:menit) ditaruh paling awal, jadi prefix berubah setiap menit
        # dan cache tidak pernah kena. Sekarang yang statis di depan,
        # yang berubah-ubah di belakang.
        parts = [sys_prompt, identity_ctx]
        if mem_str:
            parts.append(mem_str)
        # Aturan konteks: menghentikan tiga gejala yang dilaporkan pengguna —
        # topik melompat dari memori, tawaran topik di akhir jawaban, dan
        # jawaban yang mengulang tool yang sama. Dua aturan lama yang
        # bertentangan dengan [JUDGEMENT] ("bertindak, jangan tanya") dan
        # mengulang [VOICE]/[EXECUTION] sudah dibuang — instruksi yang saling
        # bertolak belakang adalah beban, bukan penjaga.
        parts.append(
            "[ATURAN KONTEKS]\n"
            "- Blok memori di atas adalah REFERENSI, bukan agenda. Jangan "
            "memulai atau mengangkat topik dari memori kecuali pengguna "
            "menyebutnya lebih dulu.\n"
            "- Setelah membahas gambar atau tangkapan layar, jangan "
            "mengungkitnya lagi kecuali pengguna bertanya tentang gambar itu.\n"
            "- Ambigu: ambil bacaan paling wajar lalu kerjakan — bertanya "
            "hanya bila tindakannya TIDAK bisa dibatalkan dan sasarannya "
            "benar-benar tidak jelas. Satu pertanyaan singkat, bukan daftar.\n"
            # GrokBot 22 Okt — anti-klaim palsu, sejalan dengan [EVIDENCE].
            "- Hasil tool bertanda [UNVERIFIED] atau [FAILED] berarti BELUM "
            "berhasil: katakan apa yang sebenarnya terjadi, jangan bilang "
            "sudah. Nama aplikasi/penerima yang tidak dikenal → tanyakan, "
            "jangan tebak yang mirip.\n"
            # 22 Okt — sumber bug "UI Nemotron tapi otak bilang DeepSeek":
            # note memori current_heavy_model tertanggal 17 Okt dipakai
            # sebagai kebenaran. Identitas model hanya dari tool.
            # 24 Okt — JARVIS menyalahkan "Google Calendar" padahal event
            # dibuat dengan +07:00. Yang benar: sebut zona yang KAMU kirim,
            # dan minta pengguna memeriksa zona tampilan kalendernya.
            "- Soal zona waktu: jangan menyalahkan kalender/aplikasi. Sebut "
            "zona waktu yang kamu kirim (mis. +07:00) dan jam yang kamu "
            "minta, lalu minta pengguna memeriksa zona tampilan di "
            "kalendernya. Tanpa membaca ulang event, jangan mengklaim "
            "kesalahan pihak lain.\n"
            "- Identitas model (model/penyedia yang sedang dipakai) HANYA "
            "dari hasil model_status. Catatan memori soal model bisa usang — "
            "jangan menjawab dari memori dan jangan mengarang nama model."
        )
        # Where the last project actually lives. Without this the assistant
        # treats every "revisi" as a fresh build and writes into a new folder,
        # which is exactly what the user kept seeing.
        try:
            from core import workspace as _workspace
            _ws = _workspace.describe()
            if _ws:
                parts.append(
                    "[FOLDER KERJA]\n" + _ws + "\n"
                    "Kalau pengguna minta revisi, perbaikan, atau penambahan "
                    "pada pekerjaan sebelumnya, tulis ke folder ITU (isi "
                    "parameter 'folder' pada dev_agent dengan path tersebut) — "
                    "jangan membuat folder baru, dan jangan menaruh file di "
                    "luar folder itu. Kalau pengguna menyebut 'folder kerja "
                    "saya', maksudnya folder ini. File baru dari tool lain "
                    "(Excel, dokumen, gambar) juga disimpan di folder itu: isi "
                    "parameter 'folder' pada tool pembuat file, jangan biarkan "
                    "jatuh ke Desktop.")
        except Exception:
            pass

        # Tools the user switched off (tool_permissions.deny), read per
        # connect so a config edit needs only a reconnect.
        try:
            _cfg2 = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._perm_cfg = _cfg2
            _perm = _cfg2.get("tool_permissions") or {}
            self._deny_tools = {str(x).strip()
                                for x in (_perm.get("deny") or [])
                                if str(x).strip()}
            if self._deny_tools:
                print("[JARVIS] 🔒 tool dimatikan: "
                      + ", ".join(sorted(self._deny_tools)))
        except Exception:
            self._perm_cfg = {}
            self._deny_tools = set()

        # Memori yang RELEVAN untuk giliran ini, bukan seluruh isinya.
        # BM25 memilih beberapa ingatan teratas dari ribuan; ini yang membuat
        # "dia ingat" tanpa membuat prompt tumbuh setiap hari.
        try:
            from core import semantic as _sem
            _ctx = _sem.context(self._live_utterance or "", k=3,
                                min_score=1.5)
            if _ctx:
                parts.append("[INGATAN RELEVAN — referensi, bukan topik]\n"
                             + _ctx)
        except Exception:
            pass
        # Fase 4 Slice 2: fakta pekerjaan latar yang sudah mendarat (cincin
        # self._landed). Hasil bicara lewat jalur suara saat terjadi; blok ini
        # yang menjaga jawaban SETELAH sesi menyambung kembali — di sana
        # konteks bicara hilang, dan "tadi gimana?" harus tetap punya jawaban
        # yang benar.
        try:
            _landed_blk = self._landed.block()
            if _landed_blk:
                parts.append(_landed_blk)
        except Exception:
            pass
        # Daftar tool yang kamu terima SENGAJA pendek (efisiensi token). Kalau
        # kemampuan yang dibutuhkan tidak terlihat, jalan satu-satunya adalah
        # `search_tools` — bukan mengarang skrip pengganti. Diuji 23 Okt: tanpa
        # aturan ini model mencoba "mengganti" excel_writer dengan code_helper
        # dan mengaku file sudah jadi.
        # Daftar tool sengaja pendek (efisiensi token) dan kanal input adalah
        # SUARA (bukan teks) — dua aturan yang diuji, lihat
        # sonde_token_efficiency.py dan sonde_voice_channel.py.
        parts.extend(_brain_extra_notes())

        # Waktu berubah setiap menit: taruh di EKOR, bukan kepala, agar
        # prefix statis di atasnya tetap bisa di-cache.
        parts.append(time_ctx)
        full_system = "\n".join(parts)

        # Hybrid engine (voice_brain = "custom"): the thinking moves to the
        # user's own provider. The mouth keeps the clock and its name, gets
        # the short voice-interface rules, and carries exactly one tool —
        # every other declaration now belongs to the brain, not to Gemini.
        if self._brain_mode:
            self._ensure_brain(full_system)
            parts = [time_ctx, _voice_interface_prompt(self._asst_name)]
            # 25 Okt — MULUT IKUT MENERIMA MEMORI sejak sesi dibuka, bukan
            # hanya saat mode curhat diaktifkan. Tanpa ini mulut harus
            # memanggil recall_memory untuk hal yang sudah diketahui, dan
            # "apa yang kamu ingat tentang saya?" dijawab "belum ada".
            # Ditaruh di EKOR supaya aturan di atasnya tetap statis.
            if mem_str:
                parts.append(mem_str)
            # Mulut memegang tool RINGAN supaya "buka youtube / cek jadwal"
            # dijawab cepat tanpa memanggil otak; aksi berat tetap lewat
            # `brain`. Tanpa ini, semua perintah kecil ikut menunggu otak.
            _all_decls = [_BRAIN_TOOL] + _voice_tool_decls(_all_decls)
        else:
            # Live mode with two tiers: the fast model keeps every tool for
            # reading/opening/quick work, and gets ONE extra door to the
            # custom brain for the writing jobs it is bad at.
            self._ensure_brain(full_system)
            parts.append(_TWO_TIER_NOTE)
            _all_decls = _all_decls + [_CUSTOM_BRAIN_TOOL]

        # Skills: the workflow rules that span several tools — the one thing a
        # tool description cannot carry (see core/skills.py). Read at build time
        # so editing a skill file takes effect on the next session, and kept to
        # a hard budget so a skill can never crowd out the conversation.
        _skills_text, self._skills_manifest = load_skills(BASE_DIR / "skills")
        if _skills_text:
            parts.append(_skills_text)
            print(f"[Skills] {len(self._skills_manifest)} loaded: "
                  + ", ".join(s["name"] for s in self._skills_manifest))

        cfg = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": _all_decls}],
            # Asks the server to issue session-resumption handles. Enabled but
            # NOT yet used: no handle is captured from the resumption update and
            # none is passed back here, so a reconnect starts a fresh session
            # rather than continuing the old one. Left on because the handles
            # cost nothing and are the prerequisite for doing it properly.
            # Hand back the handle captured from the last session_resumption
            # update. `handle=None` is exactly the old behaviour (ask for
            # handles, start fresh), so the first connect of a run is unchanged.
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            # Sliding-window compression: session never dies from a full context
            # window — JARVIS can stay in one conversation for hours
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=get_voice()
                    )
                )
            ),
        )
        if self._enhanced_live:
            # Proactive audio: JARVIS stays silent when speech isn't addressed
            # to it (background chatter, talking to someone else in the room).
            # (Affective dialog was dropped: gemini-3.1-flash-live does not
            #  support it, and it never reliably detected tone in practice.
            #  To restore it on a 2.5 native-audio model, add back:
            #  cfg["enable_affective_dialog"] = True )
            if get_proactive_audio_enabled():
                cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)

        if self._tuned_live:
            cfg.update(self._tuning_config())

        return types.LiveConnectConfig(**cfg)

    def _tuning_config(self) -> dict:
        """The optional knobs, kept apart so one bad field can be dropped wholesale.

        Every one of these is a preview-API field. If a future model release
        stops accepting any of them the connection fails at setup, so the run
        loop turns `_tuned_live` off and reconnects on the plain config rather
        than leaving the user with an assistant that will not start.
        """
        out: dict = {}

        # How long the server waits through a pause before deciding your turn is
        # over. This — not the size of the prompt — is what most of the delay
        # before a reply actually is, and the default has to suit everybody, so
        # it is necessarily cautious.
        turn = get_turn_tuning()
        if turn.get("enabled", True):
            detect = types.AutomaticActivityDetection(
                silence_duration_ms=turn["silence_ms"],
                prefix_padding_ms=turn["prefix_ms"],
            )
            if turn["end_sensitivity"] == "high":
                detect.end_of_speech_sensitivity = types.EndSensitivity.END_SENSITIVITY_HIGH
            elif turn["end_sensitivity"] == "low":
                detect.end_of_speech_sensitivity = types.EndSensitivity.END_SENSITIVITY_LOW
            if turn["start_sensitivity"] == "high":
                detect.start_of_speech_sensitivity = types.StartSensitivity.START_SENSITIVITY_HIGH
            elif turn["start_sensitivity"] == "low":
                detect.start_of_speech_sensitivity = types.StartSensitivity.START_SENSITIVITY_LOW
            out["realtime_input_config"] = types.RealtimeInputConfig(
                automatic_activity_detection=detect)

        # Screenshots and camera frames are tokenised at this resolution and then
        # stay in the session's context. 'medium' keeps on-screen text legible
        # for a fraction of a full-resolution frame.
        res = get_media_resolution()
        if res != "default":
            out["media_resolution"] = {
                "low":    types.MediaResolution.MEDIA_RESOLUTION_LOW,
                "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                "high":   types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            }[res]

        # Thinking is left at the server default deliberately. Forcing the budget
        # to zero was measured on gemini-3.1-flash-live over interleaved trials
        # and did not make the first word arrive sooner — this model does not
        # appear to deliberate on the Live path, so pinning the field only adds a
        # way for a future release to behave differently. Set "thinking_enabled"
        # in config/api_keys.json to true to let it reason instead.
        if get_thinking_enabled():
            out["thinking_config"] = types.ThinkingConfig(thinking_budget=-1)

        return out

    def _read_brain_mode(self) -> bool:
        """voice_brain = "custom" → the custom API thinks (hybrid engine).
        Read per connect so a config change needs no restart."""
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            return (str(_cfg.get("voice_brain") or "live").strip().lower()
                    == "custom")
        except Exception:
            return False

    def _read_brain_flag(self, key: str, default: bool) -> bool:
        """A boolean brain knob from the same config file, per connect."""
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            return bool(_cfg.get(key, default))
        except Exception:
            return default

    def _flag(self, key: str) -> bool:
        """Satu sakelar suara Fase 4 (config/voice_flags.json), dibaca live.

        Sengaja tidak di-cache per connect: berkas itu adalah sakelar
        rollback, mematikannya harus berlaku pada ucapan berikutnya. Berkas
        rusak/hilang jatuh ke bawaan (config.get_flags), bukan error."""
        return bool(get_flags().get(key, False))

    def _quiet_window_passed(self) -> bool:
        """True kalau pengguna sudah diam melebati jendela sunyi (30 s).

        Jamnya ucapan PENGGUNA terakhir, bukan ucapan assistant: yang
        dicegah adalah berbicara ke ruangan yang sudah kosong, bukan
        membiarkan percakapan yang sedang ramai terganggu."""
        return ((time.monotonic() - self._last_user_speech)
                >= QUIET_WINDOW_SECONDS)

    def _mark_spoke(self) -> None:
        """Kandidat C — catat bahwa JARVIS baru saja berbicara.

        Satu stempel waktu, bukan penghitung: gerbang diam hanya butuh
        "apakah kita sudah bicara setelah ucapan terakhir pengguna", dan
        perbandingan stempel itu sendiri yang membuka siklus baru ketika
        pengguna berbicara lagi. Dipanggil dari dua tempat:
        (1) set_speaking(True) — suara model benar-benar keluar dari
            speaker; jalur yang mencakup SEMUA bicara (jawaban otak, cek-in,
            pengumuman hasil, tanda terima);
        (2) plugin_say — suntikan ucapan plugin sudah terkirim: fakta
            aplikasi sudah bicara walau playback baru mulai."""
        self._last_spoke = time.monotonic()

    def _write_intent(self) -> bool:
        """True when the words the user actually said ask for writing work.

        Deterministic on purpose (core/intent.py): the split between "let the
        fast model do it" and "send it to the custom brain" must not depend on
        the fast model's opinion of itself."""
        return is_write_intent(self._live_utterance)

    # ── background jobs: survive reconnects, speak their own result ─────────

    # ── izin, agent, dan tool buatan sendiri ────────────────────────────────

    def _confirm_granted(self, name: str) -> bool:
        """Izin dianggap ada kalau (a) tool sama, (b) < 3 menit, (c) pengguna
        benar-benar mengucapkan kata setuju. Dipakai oleh policy 'confirm'."""
        pend = getattr(self, "_pending_confirm", None)
        if not pend or pend.get("tool") != name:
            return False
        if time.monotonic() - float(pend.get("ts") or 0) > 180:
            self._pending_confirm = None
            return False
        said = (self._live_utterance or "").lower()
        # GrokBot 22 Okt — dua bug diperbaiki:
        #  (1) persetujuan harus datang dari ucapan BARU, bukan kalimat
        #      perintah yang memicu permintaan izin itu sendiri (dulu model
        #      yang langsung mengulang panggilan dalam giliran yang sama
        #      lolos bila perintahnya kebetulan memuat "ya"/"ok");
        #  (2) dicocokkan sebagai KATA UTUH (core/decision_loop.is_approval):
        #      dulu `w in said` membuat "saya", "hanya", "bayar" = "ya".
        if said.strip() == str(pend.get("utt") or "").lower().strip():
            return False
        if not _decision.is_approval(said):
            return False
        self._pending_confirm = None
        # Fase 4 Slice 3 — "selalu izinkan": izin diingat PER TOOL di
        # memory/tool_trust.json (storage terpisah; config tak ditulis).
        # "ya" biasa tetap sekali pakai seperti sebelumnya. Flag
        # trust.remember mematikan pencatatan ini; keputusan allow/deny
        # tetap urut deny > confirm > auto_allow, jadi deny tak mungkin
        # dikalahkan oleh trust.
        if self._flag("trust.remember") and any(
                w in said for w in ("selalu", "always", "selamanya")):
            try:
                tool_trust.grant(name, level="always", granted_by="aris")
                print(f"[JARVIS] 🔐 trust disimpan untuk {name} "
                      "— panggilan berikutnya takkan bertanya")
            except Exception as e:                       # noqa: BLE001
                print(f"[JARVIS] trust gagal disimpan untuk {name}: {e}")
        print(f"[JARVIS] ✅ izin diterima untuk {name}")
        return True

    def _agent_tool_names(self) -> list[str]:
        """Tool yang bisa dijalankan agent di thread pekerjaan.

        Hanya action, plugin, dan MCP — tool built-in yang butuh event loop
        (screen_share, system_status) tidak dimasukkan supaya rencana tidak
        berisi langkah yang mustahil dijalankan di latar."""
        names = list(self._action_registry.names())
        names += [d["name"] for d in self._plugin_registry.get_tool_declarations()]
        names += [d["name"] for d in self._mcp.get_tool_declarations()]
        return sorted(set(names))

    def _exec_agent_tool(self, tool: str, args: dict) -> str:
        """Eksekusi satu tool dari agent (dipanggil di thread pekerjaan).

        `player` dibungkus perekam: tool yang hanya menampilkan hasil ke layar
        (weather_report dan sejenisnya) tetap memberi teks yang bisa
        diverifikasi, tanpa mengubah tool-nya satu per satu."""
        from core.agent.recorder import ContentRecorder, merge_result
        rec = ContentRecorder(self.ui)
        # GrokBot 22 Okt — hasil agent ikut diverifikasi di pintu pusat
        # (izinnya sudah diputuskan AgentRunner lewat decision_loop.gate).
        out = self._exec_agent_tool_raw(tool, args, rec, merge_result)
        _verified, out = _decision.verify(tool, args, out)
        _decision.note_outcome(tool, _verified, out)
        return out

    def _exec_agent_tool_raw(self, tool: str, args: dict, rec,
                             merge_result) -> str:
        try:
            if self._action_registry.has(tool):
                ctx = {"player": rec, "speak": None, "response": None,
                       "session_memory": None}
                return merge_result(self._action_registry.run(tool, args, ctx),
                                    rec)
            if self._plugin_registry.has(tool):
                return merge_result(self._plugin_registry.run(
                    tool, args, player=rec, session_memory=None), rec)
            if self._mcp.has(tool):
                return self._mcp.run(tool, args) or "Done."
        except Exception as e:                       # noqa: BLE001
            return f"ERROR: {type(e).__name__}: {e}"
        return f"Tool '{tool}' tidak bisa dijalankan dari latar."

    def _format_agent_result(self, res: dict) -> str:
        st = res.get("status")
        _calls = res.get("model_calls")
        _tail = f" ({_calls} panggilan model)" if _calls else ""
        if st == "done":
            msg = (f"Selesai — {res['steps_done']}/{res['steps_total']} langkah "
                   f"berhasil diverifikasi")
            if res.get("unverified"):
                msg += f" ({res['unverified']} langkah belum terverifikasi)"
            return msg + f". Tujuan: {res.get('goal', '')}.{_tail}"
        if st == "paused":
            p = res.get("pending") or {}
            step = (p.get("step") or {}).get("title", "langkah berikutnya")
            return (f"Berhenti menunggu izin pada '{step}' ({p.get('why')}). "
                    "Kemajuannya sudah disimpan — bilang 'lanjutkan' untuk "
                    "meneruskan.")
        if st == "budget":
            return (f"Berhenti karena {res.get('error')}. Kemajuan tersimpan — "
                    "bilang 'lanjutkan' kalau mau diteruskan dengan jatah baru.")
        return f"Gagal: {res.get('error') or 'tidak diketahui'}{_tail}"

    def _run_agent_task(self, task: str, max_steps: int = 10) -> str:
        """Jalankan tugas multi-langkah di latar, dengan verifikasi."""
        if self._jobs is None:
            return "Antrean pekerjaan belum siap."
        # "lanjutkan" meneruskan tugas yang tertunda, bukan memulai yang baru.
        _low = task.strip().lower()
        if any(_low.startswith(w) for w in ("lanjut", "resume", "teruskan")):
            pend = getattr(self, "_pending_agent_state", None)
            if pend:
                self._pending_agent_state = None
                self._jobs.submit(
                    f"agent: lanjutkan {str(pend.get('task'))[:40]}",
                    lambda: self._format_agent_result(
                        AgentRunner(
                            pend.get("task", ""), self._exec_agent_tool,
                            self._agent_tool_names(), config=self._perm_cfg,
                            max_steps=max_steps,
                            on_progress=lambda t: self.ui.task_progress(
                                f"Agent: {t[:64]}"),
                        ).run(state=pend)))
                return "Melanjutkan tugas yang tadi berhenti."
            # Tidak ada tugas agent — mungkin yang terputus adalah job biasa.
            if getattr(self, "_pending_jobs", None):
                _resumed = 0
                for _j in self._pending_jobs:
                    _d = _j.get("descriptor") or {}
                    if _d.get("kind") != "tool" or not _d.get("tool"):
                        continue
                    self._jobs.submit(
                        str(_j.get("title") or "lanjutkan"),
                        lambda dd=_d: self._run_action_sync(
                            dd["tool"], dd.get("args") or {},
                            step=self._job_step),
                        descriptor=_d)
                    _resumed += 1
                self._pending_jobs = []
                if _resumed:
                    self._jobs.forget_pending()
                    return (f"Melanjutkan {_resumed} pekerjaan yang tadi "
                            "terputus.")
            return "Tidak ada tugas tertunda yang bisa dilanjutkan."
        try:
            self._jobs.submit(
                f"agent: {task[:48]}",
                lambda: self._format_agent_result(
                    AgentRunner(
                        task, self._exec_agent_tool, self._agent_tool_names(),
                        config=self._perm_cfg, max_steps=max_steps,
                        on_progress=lambda t: self.ui.task_progress(
                            f"Agent: {t[:64]}"),
                    ).run()))
        except Exception as e:                       # noqa: BLE001
            return f"Tidak bisa memulai agent: {e}"
        return (f"Dimulai — saya kerjakan '{task[:70]}' sampai selesai, "
                "satu langkah demi satu, dan saya verifikasi tiap langkah. "
                "Saya kabari begitu tuntas.")

    def _reload_plugins(self) -> str:
        """Muat ulang folder plugins tanpa restart.

        Ini yang membuat agent bisa 'menulis tool sendiri': tulis berkas
        plugin, panggil ini, dan tool barunya langsung tersedia."""
        try:
            before = {d["name"] for d in
                      self._plugin_registry.get_tool_declarations()}
            self._plugin_registry = discover_plugins(
                plugins_dir=Path(__file__).resolve().parent / "plugins",
                core_tool_names=getattr(self, "_core_names", set()),
                logger=lambda m: print(f"[Plugins] {m}"),
                notify=lambda m: self.ui.write_log(f"SYS: {m}"))
            after = {d["name"] for d in
                     self._plugin_registry.get_tool_declarations()}
        except Exception as e:                       # noqa: BLE001
            return f"Gagal memuat ulang plugin: {e}"
        new = sorted(after - before)
        gone = sorted(before - after)
        parts = [f"{len(after)} plugin aktif"]
        if new:
            parts.append("baru: " + ", ".join(new))
        if gone:
            parts.append("hilang: " + ", ".join(gone))
        return "; ".join(parts) + "."

    def _switch_model(self, args: dict) -> str:
        """Voice model switch: resolve → save → rebuild the brain now.

        The brain is rebuilt in the same turn (its provider fingerprint
        changes), so the NEXT thing the user asks is already answered by the
        new model — no restart, no reconnect."""
        try:
            from memory.config_manager import (get_provider_config,
                                               list_provider_ids,
                                               save_active_provider,
                                               save_provider_config)
        except Exception as e:                       # noqa: BLE001
            return f"Pengaturan provider tidak bisa dibaca: {e}"

        providers = {}
        for pid in list_provider_ids():
            providers[pid] = get_provider_config(pid) or {}

        try:
            pid, model = _resolve_model_choice(
                str(args.get("model") or ""), str(args.get("provider") or ""),
                providers)
        except ValueError as e:
            return str(e)

        try:
            save_active_provider(pid)
            save_provider_config(pid, {"model": model})
        except Exception as e:                       # noqa: BLE001
            return f"Model ditemukan ({model} · {pid}) tapi gagal disimpan: {e}"

        # Rebuild immediately: the fingerprint changed, so this is cheap and
        # the next turn already uses the new model.
        try:
            if self._brain_system:
                self._ensure_brain(self._brain_system)
        except Exception as e:                       # noqa: BLE001
            print(f"[Switch] rebuild otak gagal: {e}")

        print(f"[JARVIS] 🔁 otak custom → {pid} · {model}")
        try:
            self.ui.write_log(f"SYS: otak custom → {pid} · {model}")
        except Exception:
            pass
        # The header chip reads the config, so it must be repainted here —
        # otherwise the UI keeps showing the previous model while the brain
        # already answers with the new one.
        try:
            self.ui.refresh_model_ui()
        except Exception:
            pass
        return (f"Siap — otak custom sekarang {model} lewat {pid}. "
                "Berlaku mulai giliran berikutnya.")

    def _toggle_screen_share(self, on: bool) -> None:
        """Start/stop share-screen coaching. Called from the HUD chip, from
        the STOP button in the panel, or from the `screen_share` tool."""
        on = bool(on)
        if on == self._screen_on:
            return
        self._screen_on = on
        try:
            self.ui.screen_share_show(on)
        except Exception:
            pass
        if not on:
            print("[Share] dihentikan")
            return

        # What the user is trying to do right now becomes the coaching goal.
        # A greeting is not a goal: "Hey Jarvis" produced hints like "JARVIS
        # sudah aktif" — noise on the panel. Only a real sentence counts.
        said = (self._live_utterance or "").strip()
        if len(said.split()) < 3:
            said = ""
        self._screen_goal = (said
                             or "bantu pengguna menyelesaikan tugas yang sedang "
                                "dikerjakan di layar ini; jawab dalam Bahasa "
                                "Indonesia")
        self._screen_hint_last = ""
        self._screen_last_png = b""
        self._screen_last_jpeg = b""
        self._screen_last_size = (0, 0)
        self._screen_err = 0
        print(f"[Share] layar dibagikan — tujuan: {self._screen_goal[:80]}")
        loop = getattr(self, "_loop", None)
        if loop is not None:
            asyncio.run_coroutine_threadsafe(self._screen_share_loop(), loop)
        else:
            print("[Share] belum ada event loop — tidak bisa mulai")

    async def _screen_share_loop(self) -> None:
        """Two independent loops — this is the fix for the lag.

        capture+encode (~60 ms) and the vision call (~1–3 s) used to be
        awaited in the SAME cycle, so the preview only advanced after the
        model had finished thinking. Split apart: the picture updates about
        twice a second, and the advice arrives on its own slower beat."""
        await asyncio.gather(self._screen_preview_loop(),
                             self._screen_advice_loop(),
                             return_exceptions=True)

    async def _stop_screen_share(self, reason: str = "") -> None:
        self._screen_on = False
        try:
            self.ui.screen_share_show(False)
        except Exception:
            pass
        if reason and self.session:
            self.plugin_say(reason + " Sampaikan singkat ke pengguna.")

    async def _screen_preview_loop(self) -> None:
        """Fast lane: screen → JPEG → panel at ~12 fps. No model involved.

        OpenCV's resize+encode measured 3.4 ms against PIL's 22.6 ms on this
        machine, which is the difference between a preview that looks live
        and one that looks like a slideshow."""
        from core import screen_share as _ss

        while self._screen_on:
            try:
                jpeg, w, h = await asyncio.to_thread(_ss.capture_fast)
                self._screen_last_jpeg = jpeg
                self._screen_last_size = (w, h)
                self._screen_err = 0
                self.ui.screen_share_frame(jpeg, "")
            except Exception as e:                       # noqa: BLE001
                self._screen_err = getattr(self, "_screen_err", 0) + 1
                print(f"[Share] tangkap layar gagal "
                      f"({self._screen_err}/3): {e}")
                if self._screen_err >= 3:
                    await self._stop_screen_share(
                        "Berbagi layar saya hentikan karena layar tidak bisa dibaca.")
                    return
                await asyncio.sleep(1.0)
                continue
            await asyncio.sleep(0.08)     # ~12 fps ceiling; encode sets the pace

    async def _screen_advice_loop(self) -> None:
        """Slow lane: one vision call every ~3.6 s on a full-resolution frame.

        The model needs detail (PNG, full size) to say WHERE something is, so
        this lane captures on its own instead of reusing the small preview."""
        from core import screen_share as _ss

        while self._screen_on:
            await asyncio.sleep(3.6)
            if not self._screen_on:
                return
            try:
                png, w, h = await asyncio.to_thread(_ss.capture)
                res = await asyncio.to_thread(
                    _ss.advise, self._screen_goal, png, w, h,
                    self._screen_hint_last)
            except Exception as e:                       # noqa: BLE001
                print(f"[Share] analisis gagal: {e}")
                continue

            hint = str(res.get("hint") or "").strip()
            if hint:
                try:
                    # Empty frame bytes = keep the picture, replace the line.
                    self.ui.screen_share_frame(b"", hint)
                except Exception:
                    pass
                if hint != self._screen_hint_last:
                    self._screen_hint_last = hint
                    print(f"[Share] petunjuk: {hint[:90]}")
                    if self.session:
                        self.plugin_say(
                            hint + "\n\nSampaikan sebagai bimbingan singkat "
                            "ke pengguna: satu kalimat, langsung bisa "
                            "dikerjakan.")

            if res.get("ok"):
                await self._stop_screen_share(
                    "Tujuan di layar sudah tercapai. Beri tahu pengguna "
                    "dengan singkat.")
                return

    def _on_job_start(self, title: str) -> None:
        """Called from the job thread when a queued job begins."""
        try:
            self.ui.task_progress(f"Latar: {str(title)[:58]}…")
            self.ui.write_log(f"SYS: pekerjaan latar dimulai — {str(title)[:70]}")
        except Exception:
            pass

    def _on_job_done(self, job) -> None:
        """Called from the job thread when a job ends.

        With a live session the result is spoken right away; without one it
        waits in a queue and is spoken at the next connect. Either way the
        work is already done — the session is not what keeps it alive.

        The wording is chosen from what ACTUALLY happened (_job_verdict),
        never from the job's exit status alone: composio_actions reports
        every failure as a sentence rather than an exception, so a finished
        job can still be an email that was never sent."""
        verdict, summary = _job_verdict(job)
        title = job.title
        # Live Task Graph: tutup node terakhir dengan hasil sebenarnya.
        try:
            _ok = verdict in ("done", "ok")
            self.ui.task_mark(f"{title[:30]} — {verdict}",
                              "ok" if _ok else "wait",
                              float(getattr(job, "elapsed", lambda: 0)() or 0) * 1000)
        except Exception:                        # noqa: BLE001
            pass
        if verdict == "waiting":
            msg = (f"Pekerjaan '{title}' menunggu konfirmasi kamu: "
                   f"{summary}")
            line = (msg + "\n\nIni belum dijalankan. Sampaikan kalimat "
                    "konfirmasinya apa adanya sebagai pertanyaan ya/tidak "
                    "ke pengguna. Jangan mengaku sudah mengirim atau "
                    "melakukan apa pun.")
        elif verdict == "failed":
            reason = " ".join((job.error or summary or job.result
                               or "tanpa keterangan").split())[:200]
            msg = f"Pekerjaan '{title}' GAGAL — {reason}"
            line = (msg + "\n\nSampaikan kegagalan ini ke pengguna dengan "
                    "jujur dan singkat: apa yang dicoba, bahwa BELUM "
                    "terjadi, dan alasinya. Jangan mengaku berhasil.")
        else:
            msg = (f"Pekerjaan '{title}' sudah selesai "
                   f"({job.elapsed():.0f} detik). {summary}")
            line = (msg + "\n\nSampaikan ini ke pengguna dengan singkat, dalam "
                    "bahasa percakapan. Jangan menambah atau mengubah faktanya. "
                    "Kalau ini menyela percakapan yang sedang berjalan, akhiri "
                    "dengan SATU jembatan 2-4 kata yang mengembalikan ke topik "
                    "itu — bukan pertanyaan, bukan topik baru.")
        # Fase 4 Slice 2: fakta yang benar-benar mendarat disimpan ke cincin
        # (dedup per id pekerjaan) supaya bisa disuntikkan ke prompt saat
        # menyambung. Cincin penuh -> entri terlama kelang sendiri.
        self._landed.add(job.id, landed_line(verdict, msg))
        try:
            self.ui.task_progress("")
            self.ui.write_log(f"SYS: {msg[:200]}")
            # Catat ke riwayat percakapan — inilah yang diurai
            # `_recent_dialogue()` dan dibaca otak pada giliran berikutnya.
            # Tanpa ini, otak menjawab "belum dikerjakan" padahal berkasnya
            # sudah ada (kejadian 24 Okt).
            try:
                self._session_log.append(
                    f"{self._asst_name}: " + _job_record(title, summary))
                if len(self._session_log) > 60:
                    del self._session_log[:-60]
            except Exception:                    # noqa: BLE001
                pass
            try:
                from core import semantic as _sem
                _sem.add(_job_record(title, summary), tags=["jobs"],
                         source="job_done")
            except Exception:                    # noqa: BLE001
                pass
        except Exception:
            pass
        # Fase 4 Slice 5 — jendela sunyi (anti-spam). Kegagalan dan
        # permintaan konfirmasi SELALU diucapkan: itu justru pesan yang
        # tidak boleh menunggu. Keberhasilan yang mendarat setelah pengguna
        # diam ≥30 s tidak diucapkan ke ruangan kosong — ditahan dan
        # disampaikan sekali sebagai [HASIL_TUNDA] pada giliran berikutnya.
        # Hasil TANPA isi (status "Done." kosong) tidak diumumkan sama
        # sekali: tak ada fakta baru untuk diucapkan. Faktanya tetap tercatat
        # di cincin _landed di atas, jadi "tadi gimana?" tetap terjawab.
        if (verdict == "done" and self._flag("voice.quiet_window")
                and self._quiet_window_passed()):
            if str(summary or "").strip() not in ("", "Done."):
                _held = landed_line(verdict, msg)
                if _held:
                    self._deferred_results.append(_held)
                    del self._deferred_results[:-DEFERRED_MAX]
            return
        # Selagi otak custom memegang giliran, jangan menyelipkan bicara ke
        # sesi Live di tengah turn — tunda sampai turn selesai.
        if self.session and not self._in_brain:
            self.plugin_say(line)
        else:
            self._pending_job_msgs.append(line)

    def _flush_pending_job_msgs(self) -> None:
        """Speak results that finished while no session was connected.

        Diam saat belum ada sesi: memanggil plugin_say tanpa sesi
        membuang pesan itu selamanya, dan pengumuman kegagalan adalah
        persis pesan yang tidak boleh hilang."""
        if not self.session:
            return
        while self._pending_job_msgs:
            self.plugin_say(self._pending_job_msgs.pop(0))

    async def _flush_cut_note(self) -> None:
        """Fase 4 barge-in — sampaikan catatan potongan, sekali pakai.

        Disuntik sebagai KONTEKS (turn_complete=False) tepat saat ucapan
        baru mulai: model sudah mendengar ucapan itu lewat audio, dan
        catatan harus berada di depannya dalam aliran yang sama supaya
        jawaban berikutnya tahu pengguna baru saja memotong di titik mana.
        Setelah disampaikan catatan dibuang — sekali pakai, kalau tidak ia
        menempel di giliran-giliran berikutnya sebagai masa lalu yang
        bukan topik."""
        note = self._cut_note
        if not note or not self.session:
            return
        self._cut_note = ""
        try:
            await self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": note}]},
                turn_complete=False,
            )
        except Exception as e:                           # noqa: BLE001
            print(f"[BargeIn] catatan potongan tak terkirim: {e}")

    async def _flush_deferred_results(self) -> None:
        """Fase 4 Slice 5 — sampaikan hasil yang ditahan, sekali, satu blok.

        Satu blok [HASIL_TUNDA], bukan sebaris per pekerjaan: lima
        pengumuman terpisah adalah spam dengan bahasa yang berbeda. Gagal
        kirim = dikembalikan ke penampung, tidak dibuang."""
        if not self._deferred_results or not self.session:
            return
        rows = list(self._deferred_results)
        _block = deferred_block(rows)
        if not _block:
            self._deferred_results.clear()
            return
        try:
            await self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": _block}]},
                turn_complete=False,
            )
            self._deferred_results.clear()
        except Exception as e:                           # noqa: BLE001
            print(f"[Ritme] hasil tunda tak terkirim: {e}")

    async def _flush_receipts(self) -> None:
        """Fase 4 Slice 2 — ucapkan tanda terima yang masih tertahan.

        Dipanggil SETELAH send_tool_response: giliran pengguna yang
        menyelip di tengah panggilan tool bisa membuat panggilan itu
        dibatalkan (barge-in), responsnya ditolak, dan sesi putus. Di
        sini sesi sudah bebas — penyuntikan aman, persis seperti
        pengumuman hasil dari _on_job_done."""
        while self._receipt_pending:
            line = self._receipt_pending.pop(0)
            if not self.session:
                # Sesi hilang di antara queue dan bicara: kembali ke antrean
                # supaya tidak ada penerimaan yang hilang tanpa jejak.
                self._pending_job_msgs.append(line)
                return
            try:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": line}]},
                    turn_complete=True,
                )
            except Exception as e:                       # noqa: BLE001
                print(f"[Receipt] gagal mengucapkan tanda terima: {e}")
                return

    def _run_action_sync(self, name: str, args: dict, step=None) -> str:
        """One action, run on the job thread (actions are synchronous).

        `step` is handed to actions that accept a `speak` callback (dev_agent
        does) so the HUD can show the current phase instead of a frozen
        title for several minutes."""
        ctx = {}
        if step is not None:
            ctx["speak"] = step
        return self._action_registry.run(name, args, ctx)

    def _run_tool_sync(self, name: str, args: dict) -> str:
        """Jalankan action ATAU plugin di thread pekerjaan.

        `_BACKGROUND_TOOLS` dulunya hanya menangani action; aksi kirim keluar
        (composio, telegram, pesan) hidup di plugin, jadi eksekutornya harus
        kedua-duanya."""
        ctx = {"speak": self._job_step}
        if self._action_registry.has(name):
            return self._action_registry.run(name, args, ctx) or "Done."
        if self._plugin_registry.has(name):
            return self._plugin_registry.run(
                name, args, player=self.ui, session_memory=None) or "Done."
        if self._mcp.has(name):
            return self._mcp.run(name, args) or "Done."
        return f"ERROR: tool '{name}' tidak tersedia di latar."

    def _should_background(self, name: str, args: dict) -> bool:
        """Apakah tool ini lebih baik dijalankan di latar?

        Ya untuk: pekerjaan berat (bangun proyek, buat dokumen) dan aksi yang
        mengirim keluar (email, pesan). Bukan untuk: pencarian, status, dan
        semacamnya — itu cepat, dan mengantreinya justru membuat jawaban jauh
        lebih lambat daripada yang diperlukan."""
        if name not in _LONG_TOOLS:
            return False
        # GrokBot 22 Okt — aksi code_helper yang cepat dan HASILNYA dibutuhkan
        # langkah berikutnya (baca/cari/ganti persis) tidak boleh diantrekan:
        # di antrean otak hanya menerima tanda terima, bukan isi berkas.
        if name == "code_helper" and str(args.get("action") or "").lower() \
                in ("read", "search", "replace"):
            return False
        if name != "composio_actions":
            return True
        action = str(args.get("action") or "").lower().strip()
        # Pencocokan memakai "mengandung", bukan "diawali": nama aksi Composio
        # berbentuk calendar_list / gmail_read_email, bukan list_calendar.
        return bool(action) and not any(w in action for w in _READ_WORDS)

    def _job_step(self, text: str) -> None:
        """Progress callback used by actions that narrate their phases.

        23 Okt: baris ini dulu hanya muncul di konsol, jadi satu-satunya cara
        memantau pekerjaan latar adalah membuka jendela hitam — tidak mungkin
        ketika JARVIS kelak berjalan sebagai aplikasi. Sekarang setiap
        langkah masuk ke HUD, dan tonggak penting juga ke activity log.
        """
        try:
            self._jobs.set_step(text)
            print(f"[Jobs] {text}")
        except Exception:                        # noqa: BLE001
            pass
        try:
            self.ui.task_progress(f"Latar: {str(text)[:70]}")
            # Setiap langkah pekerjaan latar juga masuk grafik proses.
            self.ui.task_mark(str(text)[:38], "run")
        except Exception:                        # noqa: BLE001
            pass
        low = str(text or "").lower()
        _milestone = any(k in low for k in ("written", "project:", "running",
                                            "done", "selesai", "folder kerja"))
        try:
            _now = time.monotonic()
            if _milestone or _job_step_should_log(
                    text, getattr(self, "_job_log_last", ""),
                    getattr(self, "_job_log_ts", 0.0), _now):
                self._job_log_last = str(text or "")
                self._job_log_ts = _now
                self.ui.write_log(f"SYS: {str(text)[:90]}")
        except Exception:                        # noqa: BLE001
            pass

    async def _run_job_ticker(self) -> None:
        """Keep the HUD's job line alive: elapsed time + current step.

        The brain owns that line while it is thinking; this only writes when
        it is not, so the two never fight over it."""
        while True:
            await asyncio.sleep(1.0)
            try:
                # Sesi Live yang sudah tua diputar SEBELUM server memutusnya,
                # dan hanya saat mulut tidak sedang dipakai. Pekerjaan latar
                # (dev_agent) TIDAK dihitung sibuk: ia berjalan di job sendiri
                # dan tidak butuh sesi Live.
                try:
                    _age = time.monotonic() - getattr(
                        self, "_session_started", time.monotonic())
                    with self._speaking_lock:
                        _speaking = self._is_speaking
                    _busy = (getattr(self, "_in_brain", False) or _speaking
                             or getattr(self, "_awaiting_reply", False))
                    if (self.session is not None
                            and not getattr(self, "_rotating", False)
                            and _should_rotate_session(_age, _busy)):
                        self._rotating = True
                        print(f"[JARVIS] Sesi berumur {_age:.0f}s — diputar "
                              f"sebelum batas server (menganggur).")
                        self.ui.write_log("SYS: Menyambung ulang sesi "
                                          "(preventif, sebelum batas waktu).")
                        self.request_reconnect(keep_context=True,
                                               reason="session age")
                except Exception:                    # noqa: BLE001
                    pass
                if self._jobs is None:
                    continue
                job = self._jobs.current()
                if job is None:
                    continue
                mm, ss = divmod(int(job.elapsed()), 60)
                step = f" · {job.step[:34]}" if job.step else ""
                self.ui.task_progress(
                    f"Latar: {job.title[:40]} · {mm}:{ss:02d}{step}")
            except Exception:
                pass

    async def _guarded_brain(self, utterance: str) -> str:
        """One brain turn, watched for STALLS rather than for duration.

        A big project legitimately takes minutes, so a fixed wall clock cut
        healthy work short. What actually needs catching is a provider that
        has stopped moving: the brain touches `last_progress` at every step
        and every tool result, and this loop stops the turn only when that
        stops moving for _BRAIN_IDLE_TIMEOUT. A hard ceiling remains as a
        last resort, not as the normal path.

        When it does stop, the sentence says so, says that NOTHING was
        finished, and offers the two things the user can choose — retry, or
        let the fast model do it. It never quietly falls back, because a
        quiet fallback is how a wrong answer gets sent."""
        # Perintah kendali mode ditangkap di sini juga: router bisa
        # mengalihkan ucapan ke otak sebelum cabang `brain` melihatnya.
        _cmd = _mode_command(utterance)
        if _cmd == "force":
            self._force_brain = True
            self.ui.write_log("SYS: mode otak: PAKSA CUSTOM — semua "
                              "pekerjaan lewat otak custom.")
            return ("Sudah, raja. Mulai sekarang SEMUA pekerjaan dikerjakan "
                    "otak custom. Bilang 'mode default' kalau mau kembali.")
        if _cmd == "default":
            self._force_brain = False
            self._vent_mode = False          # keluar juga dari mode curhat
            self.ui.write_log("SYS: mode otak: DEFAULT — aksi ke otak, "
                              "perintah kecil ke Gemini.")
            return ("Baik, raja. Kembali ke mode default: perintah ringan saya "
                    "kerjakan sendiri, tugas berat ke otak custom.")
        self._in_brain = True
        task = None
        try:
            task = asyncio.create_task(self._brain_turn(utterance))
            started = time.monotonic()
            # GrokBot 22 Okt — tiga bug penjaga waktu diperbaiki (nilai 120 s
            # dan 30 menit TIDAK berubah, kalimat ke pengguna sama):
            #  (1) `last` dulu diambil dari last_progress giliran SEBELUMNYA;
            #      bila itu > 120 s lalu, giliran baru langsung dinyatakan
            #      macet setelah ~1 detik. Sekarang minimal = waktu mulai.
            #  (2) objek brain dibaca ulang tiap detik — _brain_turn bisa
            #      membangun brain baru (_ensure_brain), dan kemajuan di
            #      objek baru dulu tak terlihat → dihentikan di 120 s
            #      padahal sedang bekerja.
            #  (3) batas total 30 menit dulu dilewati selama ada kemajuan
            #      (`continue` sebelum cek) — sekarang dicek lebih dulu.
            last = max(started, float(getattr(getattr(self, "_brain", None),
                                              "last_progress", 0.0) or 0.0))
            while True:
                done, _ = await asyncio.wait({task}, timeout=1.0)
                if done:
                    return task.result()
                now = time.monotonic()
                if now - started > _BRAIN_HARD_CAP:
                    task.cancel()
                    return ("Pekerjaan ini melewati batas waktu total. Saya "
                            "BELUM menyelesaikannya. Mau saya lanjutkan "
                            "sebentar lagi, atau Anda ingin saya kerjakan "
                            "dengan Gemini?")
                brain = getattr(self, "_brain", None)
                prog = float(getattr(brain, "last_progress", 0.0) or 0.0)
                if prog > last:
                    last = prog
                    continue
                if _brain_stalled(now, last,
                                  bool(getattr(brain, "in_call", False))):
                    task.cancel()
                    return (f"Otak custom berhenti bergerak selama "
                            f"{int(_BRAIN_IDLE_TIMEOUT)} detik — provider "
                            "tidak merespons. Saya BELUM menyelesaikan "
                            "pekerjaan ini. Mau saya coba lagi, atau Anda "
                            "ingin saya kerjakan dengan Gemini?")
                if now - started > _BRAIN_HARD_CAP:
                    task.cancel()
                    return ("Pekerjaan ini melewati batas waktu total. Saya "
                            "BELUM menyelesaikannya. Mau saya lanjutkan "
                            "sebentar lagi, atau Anda ingin saya kerjakan "
                            "dengan Gemini?")
        except asyncio.CancelledError:
            raise
        except Exception as e:                       # noqa: BLE001
            return (f"Otak custom tidak merespons ({e}). Saya BELUM "
                    "menyelesaikan pekerjaan ini. Mau saya coba lagi, atau "
                    "Anda ingin saya kerjakan dengan Gemini?")
        finally:
            if task is not None and not task.done():
                task.cancel()
            self._in_brain = False
            # Clear the progress line whatever happened — the sentence that
            # follows (answer or failure) is what the user reads next.
            try:
                self.ui.task_progress("")
            except Exception:
                pass
            # Pekerjaan latar yang selesai SELAMA turn ini ditunda tadi
            # (menyela sesi Live di tengah turn membuat jawaban bertumpuk).
            # Baru sekarang sesi bebas — kabarkan.
            try:
                self._flush_pending_job_msgs()
            except Exception:
                pass

    def _provider_signature(self) -> str:
        """A fingerprint of the provider settings the brain was built from.

        The brain is an object holding live LLM clients; the ladder re-reads
        config on every call, the brain cannot. Without this, picking a new
        model in the panel changed the heavy-task path but left the
        conversation on the old model — which is exactly the "I chose
        nemotron and it answered with GLM" report."""
        try:
            from memory.config_manager import (get_active_provider,
                                               get_provider_config,
                                               list_provider_ids)
            parts = [str(get_active_provider() or "")]
            for pid in list_provider_ids():
                c = get_provider_config(pid) or {}
                parts.append(f"{pid}:{c.get('base_url', '')}:"
                             f"{c.get('model', '')}")
            return "|".join(parts)
        except Exception:
            return ""

    def _ensure_brain(self, system: str) -> None:
        """Build the custom brain once per system prompt AND provider set.

        The active provider from ENGINE & PROVIDERS is the brain — the same
        object the heavy-task ladder and the cloud brain already use. When it
        cannot be built the mouth says so out loud instead of pretending to
        think, and every turn still reaches the user as a sentence.
        """
        sig = self._provider_signature()
        if (getattr(self, "_brain", None) is not None
                and self._brain_system == system
                and getattr(self, "_brain_sig", "") == sig):
            return
        brain = None
        try:
            from memory.config_manager import (get_active_provider,
                                               get_provider_config,
                                               list_provider_ids)
            from core.custom_brain import FallbackLLM
            from providers import registry
            active = (get_active_provider()
                      or (list_provider_ids()[0] if list_provider_ids() else ""))
            # Active provider first, then every other configured one — a slow
            # or quota-limited model must not be able to kill the turn when a
            # healthy one is sitting right behind it.
            order = ([active] if active else []) + [
                p for p in list_provider_ids() if p != active]
            provs = []
            for pid in order:
                try:
                    provs.append((pid, registry.build(pid,
                                                      get_provider_config(pid))))
                except Exception as e:               # noqa: BLE001
                    print(f"[Brain] provider '{pid}' tak bisa dibangun: {e}")
            if provs:
                brain = CustomBrain(FallbackLLM(provs), system)
                # Rotasi provider harus TERLIHAT: badge + satu baris log.
                try:
                    from core.custom_brain import set_fallback_hook
                    set_fallback_hook(self._on_brain_fallback)
                except Exception:                    # noqa: BLE001
                    pass
                # A very large model answers slower than the default allows.
                # 0 (the default) means "follow the provider's own WALL
                # (SECONDS) from the AI Provider dialog" — one setting then
                # governs both the heavy-task ladder and the conversation
                # brain, which is what the label implies.
                try:
                    _cfg = json.loads(open(API_CONFIG_PATH,
                                           encoding="utf-8").read())
                    _t = int(_cfg.get("brain_llm_timeout", 0) or 0)
                    if _t <= 0:
                        _pcfg = get_provider_config(active) or {}
                        _t = int(_pcfg.get("wall_s") or 0) or 45
                    brain.LLM_TIMEOUT = _brain_call_timeout(_t)
                    print(f"[Brain] sabar per panggilan: "
                          f"{brain.LLM_TIMEOUT:.0f}s "
                          f"(config {_t}s, minimum {_BRAIN_CALL_MIN_S:.0f}s)")
                except Exception:
                    pass
                print("[Brain] otak kustom siap: "
                      + ", ".join(p for p, _ in provs))
            else:
                print("[Brain] tak ada provider aktif — otak kustom tidak "
                      "bisa disiapkan")
        except Exception as e:                       # noqa: BLE001
            print(f"[Brain] otak kustom gagal disiapkan: {e}")
        self._brain = brain
        self._brain_system = system
        self._brain_sig = sig

    def _diagnostics_line(self) -> str:
        """A compact, honest status line for `system_status`.

        The console has all of this, but the user should be able to ASK:
        "apa yang sedang dikerjakan?", "model apa yang dipakai?", "folder
        kerjaku di mana?". This is the answer, assembled from live state.
        """
        bits: list[str] = []
        try:
            bits.append("mode: " + ("hibrida" if self._brain_mode else "cepat"))
        except Exception:
            pass
        try:
            from core import gemini as _g
            lb = _g.last_brain() or {}
            if lb:
                bits.append("otak: " + str(lb.get("model")
                                           or lb.get("pid") or "?"))
        except Exception:
            pass
        try:
            if self._jobs is not None:
                cur = self._jobs.current()
                if cur is not None:
                    bits.append(f"pekerjaan latar: {cur.title[:40]} "
                                f"({cur.elapsed():.0f}s)")
                else:
                    bits.append("pekerjaan latar: tidak ada")
        except Exception:
            pass
        try:
            from core import workspace as _w
            w = _w.last()
            if w:
                bits.append("folder kerja: " + str(w.get("dir")))
        except Exception:
            pass
        try:
            if self._deny_tools:
                bits.append("tool dimatikan: " + ", ".join(sorted(self._deny_tools)))
        except Exception:
            pass
        return "Diagnosa: " + " | ".join(bits) if bits else ""

    def _recent_dialogue(self, limit: int = 10) -> list[dict]:
        """The last few exchanges as chat messages.

        The brain only sees the utterances routed TO it, while the details
        the user gives in between ("subjeknya kabar, isinya apa pekerjaan
        kamu hari ini") go to the fast model. Without this the brain kept
        asking for information the user had already provided — which reads as
        the assistant forgetting the conversation."""
        out: list[dict] = []
        prefix = f"{self._asst_name}: "
        for line in self._session_log[-limit:]:
            if line.startswith("User: "):
                out.append({"role": "user", "content": line[6:]})
            elif line.startswith(prefix):
                out.append({"role": "assistant",
                            "content": line[len(prefix):]})
        return out

    def _auto_recall(self, utterance: str) -> str:
        """Fase 4 Slice 4 — kumpulkan ingatan relevan untuk ucapan ini.

        Dua sumber, keduanya lokal dan instan: memori semantik (BM25 —
        sumber yang sama dengan blok [INGATAN RELEVAN]) dan kamus long-term
        (yang dipakai tool recall_memory). Kalimat "tidak ada yang tersimpan"
        dari kamus bukan fakta, jadi tidak ikut. Dibatasi supaya catatan tak
        pernah membengkakkan prompt satu giliran."""
        parts: list[str] = []
        try:
            from core import semantic as _sem
            _sem_ctx = _sem.context(utterance, k=3, max_chars=600,
                                    min_score=1.5)
            if _sem_ctx:
                parts.append(_sem_ctx.strip())
        except Exception:
            pass
        try:
            _lt = (search_memory(utterance, limit=5) or "").strip()
            if _lt and not _lt.startswith(("Nothing stored",
                                           "I have not stored")):
                parts.append(_lt)
        except Exception:
            pass
        return ("\n\n".join(parts))[:1200]

    async def _brain_turn(self, utterance: str) -> str:
        """One hybrid turn: the custom API thinks, using the app's own tools.

        Every tool call the brain makes goes through _execute_tool — the same
        dispatcher the Live session uses — so actions, plugins, MCP servers,
        vision and every confirmation gate behave exactly as before.
        """
        # Follow the panel on the very next turn: if the user picked a new
        # model since the session started, the brain is rebuilt here rather
        # than waiting for the next reconnect.
        if self._brain_system:
            self._ensure_brain(self._brain_system)
        brain = getattr(self, "_brain", None)
        if brain is None:
            return ("Otak kustom belum siap — periksa provider aktif di "
                    "pengaturan Engine & Providers.")
        self.ui.task_progress("Otak custom: memahami permintaan…")
        # Fase 4 Slice 1 — klasifikasi giliran SEBELUM daftar tool disusun.
        # Sapaan, pamit, dan dikte bukan pekerjaan: daftar tool yang kosong
        # berarti tak ada yang bisa dipanggil, jadi tak ada aksi yang bisa
        # terpicu oleh kalimat yang memang bukan perintah. Bicara-ke-orang-
        # lain → tag diam (pemanggil membuat respons sunyi). Tipe lain —
        # termasuk unclear — lewat TANPA perubahan: klasifikasi yang tak
        # yakin tidak boleh mengubah perilaku lama.
        _kind = "work_request"
        _bisu = False
        if self._flag("voice.turn_classify"):
            _kind = classify_turn(utterance)
            print(f"[JARVIS] 🏷 giliran: {_kind}")
            if _kind == "not_for_me":
                return _NOT_FOR_ME_TAG
            _bisu = _kind in ("small_talk", "farewell", "dictation")
            if _bisu:
                # Daftar kosong harus benar-benar kosong: tool yang terdaftar
                # lewat search_tools di giliran lalu menempel di otak kalau
                # tidak dibuang (audit 1 — janji "tanpa tool" bocor).
                brain.clear_extra()
        from providers.schema import to_openai
        _all_decls = (TOOL_DECLARATIONS
                      + self._action_registry.get_tool_declarations()
                      + self._plugin_registry.get_tool_declarations())
        # Efisiensi token: kirim hanya tool inti + tool yang RELEVAN dengan
        # ucapan ini. Sisanya tetap bisa dicari lewat `search_tools`.
        decls = _core_decls(_all_decls) + _hinted_decls(_all_decls, utterance)
        if self._brain_mcp:
            # opt-in: kirim 207 tool MCP setiap giliran (beban ±400 KB).
            decls = decls + self._mcp.get_tool_declarations()
        # always: the on-demand search over those 207 tools.
        decls = decls + [_SEARCH_TOOLS]
        if _bisu:
            tools = []
        else:
            tools = to_openai(decls)

        async def _exec(name: str, args: dict) -> str:
            if name in ("brain", "custom_brain"):
                # The brain is already running — a self-call is a loop, not
                # a tool. Neither declaration reaches the brain's own list;
                # this guard is belt and braces.
                return "The brain cannot call itself — answer directly."
            if name == "search_tools":
                self.ui.task_progress("Otak custom: mencari tool yang cocok…")
                return self._search_mcp_tools(args, brain)
            class _FC:
                """The function-call shape _execute_tool expects."""
            fc = _FC()
            fc.name, fc.args, fc.id = name, args, f"brain_{name}"
            self.ui.task_progress(f"Otak custom: menjalankan {name}…")
            # Live Task Graph — inilah "reasoning" yang terlihat: setiap tool
            # yang dipakai otak muncul sebagai node, lalu ditutup dengan
            # durasi nyata. Sebelumnya hanya baris teks di konsol.
            self.ui.task_mark(name, "run")
            _t0 = time.monotonic()
            fr = await self._execute_tool(fc, _internal=True)
            self.ui.task_mark(name, "ok",
                              (time.monotonic() - _t0) * 1000.0)
            return str((getattr(fr, "response", None) or {}).get("result") or "")

        context = self._recent_dialogue()
        # The current utterance is usually already in the log (it is written
        # at turn_complete); dropping the duplicate keeps the brain from
        # reading the same sentence twice.
        if (context and context[-1].get("role") == "user"
                and str(context[-1].get("content", "")).strip()
                == utterance.strip()):
            context = context[:-1]

        # Fase 4 Slice 4 — ingat relevan OTOMATIS. Dulu "ingat namaku
        # siapa?" hanya terjawab kalau model ingat memanggil recall_memory;
        # kalau tidak, ia mengarang atau mengaku tidak tahu. Detektornya
        # deterministik (core.intent._wants_recall), temuannya disuntikkan
        # sebagai catatan [INGATAN_OTOMATIS] ke otak — referensi untuk
        # menjawab, bukan topik baru. Tidak ada temuan = catatan kosong,
        # prompt tak berubah.
        notes = ""
        # Dikte TIDAK memicu recall: isi teks yang dibacakan sering memuat
        # kata masa lalu ("tadi aku bilang…"), tapi teks itu materi, bukan
        # pertanyaan tentang ingatan.
        if (self._flag("recall.auto") and _kind != "dictation"
                and _wants_recall(utterance)):
            notes = self._auto_recall(utterance)

        # Langkah menyesuaikan jenis giliran: tiap langkah mengirim ulang
        # system + deklarasi tool, jadi percakapan pendek tidak perlu 12.
        try:
            _base_steps = int(brain._read_max_steps())
        except Exception:                        # noqa: BLE001
            _base_steps = 12
        brain.max_steps = _brain_max_steps_for(_kind, _base_steps)

        result = await brain.turn(utterance, tools, _exec, context=context,
                                  notes=notes)
        try:
            answered_by = getattr(getattr(brain, "llm", None), "last", "")
            if answered_by:
                print(f"[Brain] dijawab oleh: {answered_by}")
        except Exception:
            pass
        return result

    def _search_mcp_tools(self, args: dict, brain) -> str:
        """Pintu on-demand ke SEMUA tool yang tidak dikirim rutin.

        Mencakup 207 tool MCP DAN action/plugin yang dipangkas dari daftar
        inti (quiz, pomodoro, excel, receipt, dst). Tool yang ditemukan
        didaftarkan untuk permintaan BERIKUTNYA saja — payload satu giliran
        tetap kecil, kemampuan tetap utuh.
        """
        q = str((args or {}).get("query") or "").strip().lower()
        if not q:
            return "Sebutkan kata kunci (mis. 'decompile', 'quiz', 'excel')."
        pool = []
        try:
            pool += list(self._action_registry.get_tool_declarations())
            pool += list(self._plugin_registry.get_tool_declarations())
        except Exception:                        # noqa: BLE001
            pass
        try:
            pool += list(self._mcp.get_tool_declarations())
        except Exception as e:                   # noqa: BLE001
            print(f"[Brain] daftar tool MCP tak terbaca: {e}")
        hits = _search_decls(pool, q, limit=5)
        if not hits:
            return (f"Tak ada tool untuk '{q}'. Coba kata kunci lain "
                    "(mis. 'binary', 'function', 'excel', 'pengingat').")
        # Registered in the request's own shape (OpenAI), not the Gemini one.
        from providers.schema import to_openai
        brain.register_tools(to_openai(hits))
        lines = [f"{len(hits)} tool ditemukan — siap dipanggil "
                 "di langkah berikutnya:"]
        for d in hits:
            lines.append(json.dumps(d, ensure_ascii=False)[:1500])
        return "\n".join(lines)

    async def _redo_with_brain(self) -> None:
        """A write request that ended with a claim instead of an action.

        Nothing was called, so the dispatcher had nothing to intercept — the
        fast model simply said "sudah saya kirim". The brain is handed the
        original words now, and its answer is spoken in place of the claim.
        The utterance is cleared first so the injected answer cannot trigger
        the same check again.
        """
        utterance = (self._live_utterance or "").strip()
        if not utterance or self.session is None:
            return
        self._live_utterance = ""
        print("[JARVIS] ⚠ Jawaban tanpa aksi terdeteksi — otak custom "
              "mengambil alih")
        result = await self._guarded_brain(utterance)
        # Fase 4 Slice 1 — ucapan bukan untuk JARVIS: diam total, tak ada
        # pesan konteks yang dikirim.
        if result == _NOT_FOR_ME_TAG:
            return
        try:
            await self.session.send_client_content(
                turns={"role": "user", "parts": [{"text":
                    "[OTAK CUSTOM] " + result + "\n\n"
                    "<<INSTRUKSI UNTUKMU - JANGAN DIBACAKAN>> "
                    "Sampaikan jawaban di atas kepada pengguna apa "
                    "adanya, dalam bahasa yang sama. Jangan menambah, "
                    "mengubah, atau membacakan teks instruksi ini. "
                    "<<SELESAI INSTRUKSI>>"}]},
                turn_complete=True,
            )
        except Exception as e:                       # noqa: BLE001
            print(f"[Router] gagal menyampaikan hasil otak: {e}")

    async def _execute_tool(self, fc, _internal: bool = False) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")
        self._turn_tool_used = True

        # ── SATU KEPUTUSAN PER GILIRAN (konsolidasi 24 Okt) ──────────────
        # Dulu delapan gerbang berjalan sendiri-sendiri dan bisa saling
        # menimpa. Sekarang core.decide.decide_turn memutuskan SATU hal, dan
        # blok ini hanya menjalankan keputusan itu.
        _now = time.monotonic()
        _offer = getattr(self, "_cc_offer", None)
        _offer_fresh = bool(_offer and _cc_offer_fresh(_offer.get("ts", 0.0),
                                                       _now))
        _utt = str(args.get("utterance") or args.get("request")
                   or self._live_utterance or "").strip()
        _dec = decide_turn(
            _utt,
            name=name,
            is_builtin=(name in _BUILTIN_TOOLS),
            in_brain=bool(getattr(self, "_in_brain", False)),
            internal=bool(_internal),
            force_brain=bool(getattr(self, "_force_brain", False)),
            offer_fresh=_offer_fresh,
            offer_choice=(_cc_choice(self._live_utterance or "")
                          if _offer_fresh else None),
            mode_command=_mode_command(_utt),
            small_turn=_is_small_turn(_utt),
            write_intent=(self._write_intent()
                          and not str(args.get("confirm") or "").strip()),
            gemini_allowed=allows_gemini(self._live_utterance),
            vent_mode=bool(getattr(self, "_vent_mode", False)),
        )
        print(f"[JARVIS] 🧭 putusan: {_dec['action']} ({_dec['reason']})")

        if _dec["action"] == DEC_OFFER:
            _pick = _cc_choice(self._live_utterance or "")
            self._cc_offer = None
            if _pick == "brain":
                print("[JARVIS] 🖱 tawaran: pengguna memilih OTAK CUSTOM")
                _res = await self._guarded_brain(_offer.get("utterance") or "")
                return types.FunctionResponse(
                    id=fc.id, name=name, response={"result": _res})
            self._cc_approved = True             # izin sekali pakai
            print("[JARVIS] 🖱 tawaran: pengguna memilih GEMINI")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": (
                    "Pengguna memilih kamu yang mengerjakan. Jalankan "
                    "computer_control sekarang dengan argumen yang sama "
                    "seperti tadi, lalu laporkan hasilnya apa adanya.")})

        if _dec["action"] == DEC_MODE:
            if _mode_command(_utt) == "force":
                self._force_brain = True
                self.ui.write_log("SYS: mode otak: PAKSA CUSTOM — semua "
                                  "pekerjaan lewat otak custom.")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": (
                        "Sudah, raja. Mulai sekarang SEMUA pekerjaan dikerjakan "
                        "otak custom. Bilang 'mode default' kalau mau kembali.")})
            self._force_brain = False
            self.ui.write_log("SYS: mode otak: DEFAULT — aksi ke otak, "
                              "perintah kecil ke Gemini.")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": (
                    "Baik, raja. Kembali ke mode default: perintah ringan saya "
                    "kerjakan sendiri, tugas berat ke otak custom.")})

        if _dec["action"] == DEC_SMALL:
            print("[JARVIS] 💬 obrolan ringan — otak tidak dipanggil")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": (
                    "Ini obrolan ringan, BUKAN tugas. Jawab sendiri dengan "
                    "satu atau dua kalimat, dalam bahasa pengguna. Jangan "
                    "memanggil brain lagi, jangan menyebut tool.")})

        if _dec["action"] in (DEC_FORCE, DEC_BRAIN) and name not in _BUILTIN_TOOLS:
            _src = "mode paksa" if _dec["action"] == DEC_FORCE else "niat menulis"
            print(f"[JARVIS] ↪ {name} dialihkan ke otak ({_src})")
            self.ui.task_progress("Mengalihkan ke otak custom…")
            _res = await self._guarded_brain(_utt or (self._live_utterance or ""))
            return types.FunctionResponse(
                id=fc.id, name=name, response={"result": _res})

        if (name == "computer_control" and not _internal
                and not getattr(self, "_in_brain", False)):
            if getattr(self, "_cc_approved", False):
                self._cc_approved = False        # dipakai sekali
            elif _cc_is_heavy(args):
                self._cc_offer = {"ts": _now,
                                  "utterance": (self._live_utterance or ""),
                                  "action": str(args.get("action") or "")}
                print("[JARVIS] 🖱 aksi berat — menawarkan pilihan")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": (
                        "BELUM dijalankan. Aksi ini mengontrol mouse/keyboard "
                        "dan dianggap berat. Tanyakan ke pengguna dengan satu "
                        "kalimat, bahasa yang sama: mau dikerjakan saya "
                        "(Gemini) yang cepat, atau dialihkan ke otak custom "
                        "yang lebih teliti? Tunggu jawabannya, jangan "
                        "menjalankan apa pun sekarang.")})

        # While the screen is shared, the coaching loop already captures and
        # analyses it every few seconds. A second capture path just spams the
        # vision cooldown and burns tokens for the same picture.
        if name == "screen_process" and self._screen_on:
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": (
                    "Layar sedang dibagikan — saya sudah melihatnya dan "
                    "memberi petunjuk di panel SHARE SCREEN. Jangan panggil "
                    "screen_process lagi; gunakan petunjuk terakhir yang "
                    "sudah tampil.")})

        _t0 = time.monotonic()
        # Satu tempat memutuskan boleh/tidaknya sebuah aksi
        # (core/agent/permissions.py): allow / confirm / deny.
        # Fase 4 Slice 3: args diteruskan supaya trust dengan args_hash
        # hanya berlaku untuk argumen yang sama.
        # GrokBot 22 Okt — pintu keputusan pusat (core/decision_loop.gate):
        # izin + trust + whitelist klik "coba lagi" pada agen koding.
        # Semua jalur lewat sini: suara Gemini Live, otak kustom
        # (_internal=True), teks HUD, Telegram teks bebas, dashboard.
        decision, why = _decision.gate(
            name, args, config=self._perm_cfg,
            known=name in getattr(self, "_known_tools", set()),
            internal=bool(_internal))
        if decision == "deny":
            print(f"[JARVIS] ⛔ {name} ditolak — {why}")
            _agent_audit.record(name, args, actor="brain", decision="deny",
                                ok=False, summary=why)
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": (
                    f"Tool '{name}' dimatikan oleh pengaturan pengguna ({why}). "
                    "Sampaikan ini ke pengguna, jangan mencoba tool lain untuk "
                    "tujuan yang sama.")})

        if decision == "confirm" and not self._confirm_granted(name):
            self._pending_confirm = {"tool": name, "why": why,
                                     "args": args, "ts": time.monotonic(),
                                     # GrokBot: ucapan pemicu, lihat
                                     # _confirm_granted.
                                     "utt": self._live_utterance or ""}
            _agent_audit.record(name, args, actor="brain", decision="confirm",
                                ok=False, summary="menunggu izin: " + why)
            print(f"[JARVIS] ✋ {name} menunggu konfirmasi — {why}")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": (
                    f"KONFIRMASI DIPERLUKAN untuk '{name}' ({why}). Sebutkan "
                    "ke pengguna apa yang akan dilakukan, lalu tunggu jawaban. "
                    "Jangan mengaku sudah melakukan apa pun. Kalau pengguna "
                    "setuju, panggil tool ini lagi — izinnya akan diterima.")})

        # While the custom brain is working, the fast model may not start
        # anything else — it waits for the result. The brain's own tool calls
        # arrive with _internal=True and are let through.
        if self._in_brain and not _internal and name not in ("brain",
                                                             "custom_brain"):
            print(f"[JARVIS] ⛔ {name} ditahan — otak custom masih bekerja")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": (
                    "Otak custom masih mengerjakan permintaan ini. Jangan "
                    "mengambil tindakan lain; tunggu hasilnya.")})

        # ── deterministic router ────────────────────────────────────────────
        # The user's own words decide, not the Live model's opinion. A
        # writing verb (kirim/buat/edit/ubah/hapus/jadwalkan…) means the task
        # belongs to the custom brain — so whatever tool the fast model tried
        # to call, it is intercepted here and handed over instead. Built-in
        # bookkeeping tools and the brain's own calls (_in_brain) are exempt.
        _BUILTINS = {"brain", "custom_brain", "save_memory", "recall_memory",
                     "model_status", "undo", "screen_process", "close_camera",
                     "screen_share", "switch_model", "system_status",
                     "manage_monitor", "shutdown_jarvis", "agent_run",
                     "reload_plugins"}
        if (name not in _BUILTINS and not self._in_brain and not _internal
                and (_should_force_brain(getattr(self, "_force_brain", False),
                                         name, False)
                     or (self._write_intent()
                         and not allows_gemini(self._live_utterance)
                # A call carrying confirm="ya" is the SECOND half of an action
                # the user already approved — "ya, kirim sekarang" also
                # contains a writing verb, and hijacking it here is what made
                # a ready email never send while the brain was asked to redo
                # the whole thing. The plugin's own gate validates the token.
                and not str(args.get("confirm") or "").strip()))):
            utterance = (self._live_utterance or "").strip()
            print(f"[JARVIS] ↪ {name} dialihkan ke otak custom "
                  f"(perintah tulis: “{utterance[:50]}”)")
            self.ui.task_progress("Mengalihkan ke otak custom…")
            result = await self._guarded_brain(utterance)
            # Fase 4 Slice 1 — bicara-ke-orang-lain: respons sunyi, sesi
            # tidak diberi kalimat apa pun untuk diucapkan. Nilai hasilnya
            # adalah tag itu sendiri: begitu pun kalau model tetap terdorong
            # bicara, aturan [TIDAK_ADA_JAWABAN] di prompt yang menahannya.
            if result == _NOT_FOR_ME_TAG:
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": _NOT_FOR_ME_TAG, "silent": True})
            print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name, response={"result": result})

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
                # Salinan ke memori yang bisa dicari: long_term.json dibaca
                # utuh di setiap sesi, sedangkan yang ini dicari per giliran —
                # jadi fakta lama tetap bisa ditemukan tanpa membebani prompt.
                try:
                    from core import semantic as _sem
                    _sem.add(f"{key}: {value}", tags=[category],
                             source="save_memory")
                except Exception:
                    pass
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        # Heavy work belongs on the job queue: it survives a session
        # reconnect, it does not hold the microphone, and the result is
        # announced when it is actually ready.
        if (self._should_background(name, args)
                and self._jobs is not None):
            title = (f"{name}: " + str(args.get("description")
                                       or args.get("request")
                                       or args.get("query") or "")[:70])
            # Penjaga anti-duplikasi (24 Okt): dua email identik sempat
            # terkirim karena otak mengirim ulang. Bandingkan pekerjaan yang
            # PERSIS sama dalam 60 detik — bukan kemiripan, bukan tebakan.
            try:
                import json as _json
                _sig = f"{name}|" + _json.dumps(args, sort_keys=True,
                                                ensure_ascii=False,
                                                default=str)[:600]
                _now = time.monotonic()
                if _is_duplicate_job(getattr(self, "_last_job_sig", ""), _sig,
                                     _now - float(getattr(self,
                                                          "_last_job_at", 0.0))):
                    print("[JARVIS] ⛔ pekerjaan identik — dilewati")
                    return types.FunctionResponse(
                        id=fc.id, name=name,
                        response={"result": (
                            "Pekerjaan yang PERSIS sama baru saja dijalankan. "
                            "JANGAN mengulanginya. Katakan satu kalimat ke "
                            "pengguna: ini sudah dikirim/dikerjakan tadi.")})
                self._last_job_sig = _sig
                self._last_job_at = _now
            except Exception:                        # noqa: BLE001
                pass
            job_id = self._jobs.submit(
                title,
                lambda n=name, a=dict(args): self._run_tool_sync(n, a),
                # Resep yang bisa ditulis ke berkas: kalau aplikasi ditutup di
                # tengah pekerjaan, ini yang ditawarkan untuk dilanjutkan.
                descriptor={"kind": "tool", "tool": name,
                            "args": dict(args)})
            print(f"[Jobs] {job_id} diantrekan — {title[:70]}")
            # Live Task Graph: pekerjaan baru = grafik bersih + node pertama.
            try:
                self.ui.task_clear()
                self.ui.task_mark(title[:38], "run")
            except Exception:                    # noqa: BLE001
                pass
            # Fase 4 Slice 2 — tanda terima milik kode. Dulu kalimat
            # penerimaan disusun MODEL ("saya akan mengabari setelah
            # terkirim"): ucapan untuk pekerjaan yang mungkin saja belum
            # berjalan. Sekarang aplikasi mengucapkannya sendiri (bertagar
            # [PEKERJAAN_DITERIMA]) dan respons ke model dibuat sunyi —
            # kalau model juga bicara, penerimaan terdengar dua kali.
            # Urutan penting: ucapan diantre dulu, dilepas SETELAH respons
            # tool terkirim (_flush_receipts di pemanggil). Menyelipkan
            # giliran pengguna di tengah panggilan tool bisa membuat
            # panggilan itu dibatalkan dan responsnya ditolak — sesi putus.
            # Jalur otak custom (giliran dipegang otak, atau sesi belum ada)
            # TIDAK disentuh: kalimat diteruskan sebagai hasil tool seperti
            # biasa, hanya tanpa tag internalnya.
            _desc = str(args.get("description")
                        or args.get("request")
                        or args.get("query") or "")
            if (self._flag("voice.receipt_enabled")
                    and getattr(self, "_loop", None) and self.session
                    and not self._in_brain):
                self._receipt_pending.append(compose_receipt(title, _desc))
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": "ok", "silent": True})
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": (
                    compose_receipt(title, _desc)
                    + " (sudah berjalan di latar belakang, tahan reconnect; "
                      "hasilnya akan dikabari)")})

        # Kemajuan yang terlihat. Tool yang jalan langsung bisa memakan waktu
        # lama (kirim email, baca berkas besar); tanpa baris ini HUD diam dan
        # pengguna tidak tahu apakah masih bekerja. Baris ini TIDAK dipakai
        # saat giliran dipegang otak custom — dia sudah punya baris sendiri.
        if not self._in_brain:
            try:
                self.ui.set_state("WORKING")
                self.ui.task_progress(f"Menjalankan {name}…")
            except Exception:
                pass

        if name in ("brain", "custom_brain"):
            # brain        — hybrid engine: the mouth hands everything over.
            # custom_brain — live engine: only the writing/editing jobs come
            #                here, so reading stays on the fast path.
            utterance = str(args.get("utterance") or args.get("request")
                            or "").strip()
            # Ucapan sepele & perintah mode SUDAH diputuskan di
            # dispatch terpusat di awal _execute_tool (konsolidasi
            # 24 Okt). Di sini tidak ada gerbang lagi.
            if getattr(self, "_in_brain", False):
                # A second brain turn while the first is still running gives
                # two turns competing for one mouth: the user hears silence,
                # then an answer to the older question. The mouth says one
                # line instead, so the wait is never unexplained.
                result = ("Otak custom masih mengerjakan permintaan "
                          "sebelumnya. Katakan satu kalimat singkat ke "
                          "pengguna, bahasa yang sama: masih dikerjakan, "
                          "sebentar lagi. Jangan memanggil tool.")
            else:
                result = await self._guarded_brain(utterance)
            # Fase 4 Slice 1 — bicara-ke-orang-lain: respons sunyi (lihat
            # komentar sama di cabang alih-tulis di atas).
            if result == _NOT_FOR_ME_TAG:
                print("[JARVIS] 🤐 ucapan bukan untuk JARVIS — diam")
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": _NOT_FOR_ME_TAG, "silent": True})
            print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
            # Jaring pengaman mulut: jawaban ini SUDAH ada. Kalau server
            # membatalkan giliran jawaban (VAD/keepalive), aplikasi yang
            # mengucapkannya — lihat _mouth_watchdog. Tag diam tidak ikut.
            if result and result != _NOT_FOR_ME_TAG:
                self._mouth_pending = (time.monotonic(), str(result))
                self._awaiting_reply = True
            return types.FunctionResponse(
                id=fc.id, name=name, response={"result": result})

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "recall_memory":
                # Local file search: no network, no second model. Kept out of
                # the executor deliberately — it is a dictionary scan over a few
                # hundred short strings, and a thread hop would cost more than
                # the work itself.
                result = search_memory(args.get("query", ""), limit=8)

            elif name == "model_status":
                # The honest answer: the badge and this tool read the same
                # record of who answered last (core/gemini.last_brain).
                from memory.config_manager import (get_active_provider,
                                                   get_provider_config,
                                                   list_provider_ids)
                live = str(globals().get("LIVE_MODEL", "") or "").replace("models/", "")
                pid = (get_active_provider()
                       or (list_provider_ids()[0] if list_provider_ids() else ""))
                pcfg = get_provider_config(pid) if pid else {}
                lb = _gemini.last_brain()
                if getattr(self, "_brain_mode", False):
                    bits = [f"Suara: Gemini {live or 'Live'} (mode hibrida)"]
                    if pid and pcfg.get("model"):
                        bits.append(f"Otak percakapan: {pcfg['model']} "
                                    f"lewat {pid}")
                else:
                    bits = [f"Percakapan dan suara: Gemini {live or 'Live'}"]
                    if pid and pcfg.get("model"):
                        bits.append(f"Otak tugas berat: {pcfg['model']} "
                                    f"lewat {pid}")
                if lb.get("kind") == "provider":
                    bits.append(f"Tugas terakhir dikerjakan: {lb['model']} "
                                f"({lb['pid']})")
                elif lb.get("kind") == "gemini":
                    bits.append(f"Tugas terakhir dikerjakan: Gemini "
                                f"{str(lb['model']).replace('models/', '')}")
                # 24 Okt — KESEHATAN provider, bukan hanya konfigurasi.
                # Tanpa bagian ini, model membaca "Otak percakapan: X lewat
                # flagship" lalu menyimpulkan "aman" padahal flagship sedang
                # kena HTTP 429 (free usage limit) dan river yang menjawab.
                try:
                    _llm = getattr(getattr(self, "_brain", None), "llm", None)
                    _h = _llm.health() if hasattr(_llm, "health") else []
                except Exception:                    # noqa: BLE001
                    _h = []
                _rest = [x for x in _h if x.get("resting_s", 0) > 0]
                if _rest:
                    bits.append("Provider sedang istirahat: " + "; ".join(
                        f"{x['pid']} ({x['resting_s']:.0f} detik, "
                        f"{(x.get('error') or 'tanpa keterangan')[:70]})"
                        for x in _rest))
                    _alive = [x for x in _h if x.get("resting_s", 0) <= 0]
                    if _alive:
                        bits.append("Yang masih bisa menjawab: " + ", ".join(
                            f"{x['pid']} ({x['model']})" for x in _alive))
                    bits.append("Jangan sebut model yang sedang istirahat "
                                "sebagai aman")
                elif _h:
                    bits.append("Semua provider merespons.")
                result = "; ".join(bits) + "."

            elif name == "undo":
                if str(args.get("action", "")).lower().strip() == "list":
                    items = undo_stack.history()
                    result = ("Things I can undo, most recent first:\n"
                              + "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))
                              ) if items else "I have not changed anything I can undo yet."
                else:
                    result = await loop.run_in_executor(None, undo_stack.undo_last)

            elif name == "agent_run":
                _task = str(args.get("task") or "").strip()
                if not _task:
                    result = "Sebutkan tugasnya dulu."
                else:
                    try:
                        _steps = int(args.get("max_steps") or 10)
                    except Exception:
                        _steps = 10
                    result = self._run_agent_task(_task, max(1, min(12, _steps)))

            elif name == "reload_plugins":
                result = self._reload_plugins()

            elif name == "switch_model":
                result = self._switch_model(args)

            elif name == "switch_engine":
                _mode = str(args.get("mode") or "").strip().lower()
                try:
                    from memory.config_manager import set_voice_brain
                    set_voice_brain(_mode)
                    result = (
                        "Mode mesin sekarang: HIBRIDA — Gemini jadi telinga & "
                        "suara, otak = provider custom. Saya sambungkan ulang "
                        "sekarang." if _mode == "custom" else
                        "Mode mesin sekarang: CEPAT — Gemini berpikir dan "
                        "berbicara. Saya sambungkan ulang sekarang.")
                    # The mode is read per connect, so saying it without a
                    # reconnect left the old engine running — which reads as
                    # "the switch does nothing". Give the sentence a moment to
                    # be spoken, then rebuild the session.
                    try:
                        loop.call_later(3.0, lambda: self.request_reconnect(
                            keep_context=True, reason="mode mesin → " + _mode))
                    except Exception:                     # noqa: BLE001
                        pass
                except Exception as e:                   # noqa: BLE001
                    result = f"Gagal mengganti mode mesin: {e}"

            elif name == "screen_share":
                act = str(args.get("action") or "").lower().strip()
                if act not in ("on", "off"):
                    result = "Sebutkan action 'on' atau 'off'."
                else:
                    self._toggle_screen_share(act == "on")
                    result = ("Berbagi layar AKTIF — saya melihat layar dan "
                              "membimbing langkah berikutnya. Saya tidak "
                              "mengklik apa pun." if act == "on"
                              else "Berbagi layar dihentikan.")

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    # The image is attached to this same exchange, so there is
                    # nothing to stall for and nothing to announce. Asking for an
                    # acknowledgement here is what produced two spoken answers —
                    # the model filled that turn by answering the question from
                    # imagination, then answered it again once it could see.
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured and attached to this "
                        f"same exchange. Do not acknowledge and do not answer yet — the image "
                        f"is arriving with this result. Reply once, from what you actually see "
                        f"in it."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r) + "\n" + self._diagnostics_line()

            elif name == "manage_monitor":
                action = args.get("action", "").lower().strip()
                topic  = args.get("topic", "").strip()
                if action == "add" and topic:
                    result = await asyncio.to_thread(add_monitor, topic)
                elif action == "remove" and topic:
                    result = await asyncio.to_thread(remove_monitor, topic)
                elif action == "list":
                    topics = await asyncio.to_thread(list_monitors)
                    result = ("Monitoring: " + ", ".join(topics)) if topics else "No topics are being monitored."
                else:
                    result = "Specify action (add/remove/list) and a topic."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                async def _do_shutdown():
                    await self._save_session_summary()
                    if self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"role": "user", "parts": [{"text": "Say a brief natural goodbye to the user."}]},
                                turn_complete=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                    import os as _os
                    _os._exit(0)
                asyncio.create_task(_do_shutdown())

            elif self._action_registry.has(name):
                # file_processor: fall back to the currently-uploaded file when none is given
                if name == "file_processor" and not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                _ctx = {"player": self.ui, "speak": self.speak,
                        "response": None, "session_memory": None}
                r = await loop.run_in_executor(None, lambda: self._action_registry.run(name, args, _ctx))
                result = r or "Done."
                # web_search: mirror results to the on-screen content panel
                if (name == "web_search" and r
                        and not r.startswith("No results")
                        and not r.startswith("Search failed")):
                    _mode  = args.get("mode", "search")
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)

            elif self._mcp.has(name):
                # external MCP tool servers (REA, …) — blocking, no speaker context
                r = await loop.run_in_executor(None, lambda: self._mcp.run(name, args))
                result = r or "Done."

            else:
                if self._plugin_registry.has(name):
                    r = await loop.run_in_executor(
                        None,
                        lambda: self._plugin_registry.run(name, args, player=self.ui, session_memory=None)
                    )
                    result = r or "Done."
                else:
                    result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        if not self._in_brain:
            # Selesai → baris kemajuan dilepas lagi.
            try:
                self.ui.task_progress("")
            except Exception:
                pass

        # GrokBot 22 Okt — verifikasi di pintu pusat: klaim berhasil yang
        # tidak terbukti diberi catatan [UNVERIFIED] untuk model, dan hasilnya
        # dicatat supaya relay Telegram tidak meneruskan klaim palsu.
        try:
            _verified, result = _decision.verify(name, args, result)
            _decision.note_outcome(name, _verified, result)
        except Exception:
            # verify() dirancang tak melempar; ini pengaman terakhir agar
            # sentinel kontrak tak pernah bocor ke model bila terjadi.
            result = _decision.strip_contract(result)

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        # Jejak yang bisa dipertanggungjawabkan: siapa, tool apa, boleh/tidak,
        # berapa lama, hasil ringkasnya. Kegagalan audit tidak boleh
        # menghentikan pekerjaan (record() menelan exception).
        try:
            _bad = str(result).startswith(("Tool '", "ERROR", "Unknown tool"))
            _agent_audit.record(
                name, args, actor="brain", decision=decision, ok=not _bad,
                ms=int((time.monotonic() - _t0) * 1000),
                summary=str(result)[:220])
        except Exception:
            pass

        # A tool that declared itself NON_BLOCKING also says when its answer may
        # re-enter the conversation. Without this the model finishes whatever it
        # was saying and then reads the result out on top of it — which, for
        # something like a phone call already ringing, is exactly the noise the
        # non-blocking call was meant to avoid. Tools that declared nothing get
        # the API default and behave as they always have.
        _sched = (self._action_registry.scheduling(name)
                  or self._plugin_registry.scheduling(name)
                  or self._mcp.scheduling(name))
        _extra = {"scheduling": _sched} if _sched else {}
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result},
            **_extra
        )

    async def _mouth_watchdog(self) -> None:
        """Ucapkan jawaban otak bila mulut tidak bersuara.

        23 Okt: laporan "otak menjawab tetapi JARVIS tidak bersuara" — log
        berhenti di `📤 brain → …` tanpa baris JARVIS. Server bisa membatalkan
        giliran jawaban (VAD membaca derau sebagai pengguna bicara; terukur
        DIAM 1/5 pada start-sensitivity high). Jawaban otak sudah ada di
        tangan, jadi tidak boleh hilang: kalau tidak ada audio/transkrip
        dalam masa tenggang, aplikasi mengirimkannya lewat jalur yang sama
        dengan router tulis — sudah terbukti membuat mulut bersuara.
        """
        hard_stop = time.monotonic() + _MOUTH_GRACE_S + 8.0
        try:
            while time.monotonic() < hard_stop:
                await asyncio.sleep(0.5)
                pend = getattr(self, "_mouth_pending", None)
                if pend is None:
                    return                      # mulut bersuara, beres
                since, said = pend
                with self._speaking_lock:
                    speaking = self._is_speaking
                if speaking:
                    return                      # sedang bersuara
                if not _mouth_fallback_due(since, time.monotonic(),
                                           got_audio=False):
                    continue
                self._mouth_pending = None
                print("[JARVIS] 🗣 mulut tidak bersuara — jawaban otak "
                      "diucapkan aplikasi")
                try:
                    await self.session.send_client_content(
                        turns={"role": "user", "parts": [{"text":
                            "[OTAK CUSTOM] " + said + "\n\n"
                            "<<INSTRUKSI UNTUKMU - JANGAN DIBACAKAN>> "
                            "Sampaikan jawaban di atas kepada pengguna apa "
                            "adanya, dalam bahasa yang sama. Jangan "
                            "menambah, mengubah, atau membacakan teks "
                            "instruksi ini. <<SELESAI INSTRUKSI>>"}]},
                        turn_complete=True,
                    )
                except Exception as e:                   # noqa: BLE001
                    print(f"[JARVIS] gagal mengucapkan jawaban otak: {e}")
                return
        finally:
            # Mic tidak boleh tertutup selamanya karena penjaga ini.
            self._awaiting_reply = False

    async def _send_realtime(self):
        # Loop ini adalah SATU-SATUNYA konsumen out_queue. Kalau ia berhenti,
        # produsen (mic) terus mengisi antrean sampai penuh dan setiap frame
        # berikutnya melempar QueueFull. Jadi saat koneksi mati ia TIDAK
        # keluar — ia membuang frame sampai sesi baru siap.
        while True:
            msg = await self.out_queue.get()
            if getattr(self, "_live_down", False):
                continue                          # koneksi mati: buang frame
            # Gemini 3.x Live rejects the old realtime_input.media_chunks field
            # (what `media=...` maps to) and closes the socket with a 1007. Send
            # mic / phone PCM through the new `audio` field instead. Queue items
            # are {"data": <bytes>, "mime_type": <str>} from _listen_audio and
            # the phone relay.
            try:
                await self.session.send_realtime_input(
                    audio=types.Blob(
                        data=msg["data"],
                        mime_type=msg.get("mime_type", "audio/pcm"),
                    )
                )
            except Exception as e:                   # noqa: BLE001
                if _is_conn_closed(e):
                    if not getattr(self, "_live_down", False):
                        print("[JARVIS] Koneksi Live terputus saat kirim audio — "
                              "menunggu sambung ulang (frame dibuang).")
                    self._live_down = True
                    # 24 Okt: dulu hanya menandai mati lalu diam. Kalau jalur
                    # terima juga tidak melempar, tidak ada yang memulihkan
                    # dan JARVIS membeku sampai di-restart.
                    try:
                        _now = time.monotonic()
                        if _should_ask_reconnect(
                                True, bool(getattr(self, "_reconnect_asked", False)),
                                _now, float(getattr(self, "_reconnect_asked_at", 0.0))):
                            self._reconnect_asked = True
                            self._reconnect_asked_at = _now
                            self.request_reconnect(keep_context=True,
                                                   reason="send path lost")
                    except Exception:                # noqa: BLE001
                        pass
                    continue                         # tetap jadi konsumen
                raise

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            # ── Wake-word gate ───────────────────────────────────────────────
            # While asleep, the mic audio NEVER goes to Gemini (nothing is
            # streamed, so JARVIS can't respond to speech not addressed to it and
            # nothing leaves the machine). Frames are instead handed to the local
            # detector, which runs its model in ITS OWN thread — the cost here is
            # only a queue push, so the audio path is never slowed. When wake word
            # is off (default) or we're awake, this is a single boolean check.
            if self._wake_enabled and not self._awake:
                det = self._wake_detector
                if det is not None and det.ready:
                    det.feed(indata)
                    # Remember the last few seconds — see wake(): the frames
                    # that carry the wake word are never streamed, so the
                    # sentence spoken with it must travel along.
                    self._sleep_buf.append(indata.copy())
                    if len(self._sleep_buf) > self._SLEEP_BUF_FRAMES:
                        del self._sleep_buf[:-self._SLEEP_BUF_FRAMES]
                else:
                    self._warn_wake_deaf()
                return
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking

            # ── Barge-in ─────────────────────────────────────────────────────
            # While JARVIS talks the mic is not streamed, but it is still worth
            # listening to locally: if the user starts speaking, cut the answer
            # short the way a person would stop when interrupted.
            #
            # The whole difficulty is echo — on speakers the mic hears JARVIS.
            # So the test is not "is the mic loud" but "is the mic louder than
            # the echo of what we are playing right now", sustained long enough
            # that a cough or a keystroke cannot trigger it.
            if jarvis_speaking:
                # Nothing is streamed while JARVIS talks.
                #
                # Interrupting by voice used to live here: `EchoGuard` can pick a
                # user out from under our own echo, and `core/echo.py` still does
                # that for the tail below. Re-enabling is small — classify each
                # block here and call interrupt() after `required_blocks` of
                # agreement — but it depends on the listener's room, so it stays
                # out until it can be tried on real hardware.
                return

            # ── Echo tail ────────────────────────────────────────────────────
            # The speaking flag has dropped but the speakers have not finished.
            # Sending this to the model is how an assistant hears itself, decides
            # it was addressed, and answers its own last sentence. The microphone
            # stays OPEN — the guard only drops blocks that are our own voice, so
            # replying the instant it stops still works.
            if self._tail_active():
                try:
                    if not self._echo.is_user_speech(
                            indata, SEND_SAMPLE_RATE, _pcm_level(indata)):
                        return
                    self._tail_until = 0.0      # a real voice ends the tail early
                except Exception:
                    return
            elif self._echo._hist:
                self._echo.reset()

            # ── Push-to-talk ─────────────────────────────────────────────────
            # When it is on the microphone is closed by default and the chord
            # opens it, which is the whole point: nothing leaves the machine
            # unless you are holding the key.
            if self._ptt_enabled and not self._ptt_held:
                return
            
            if not self.ui.muted and not self._phone_active:
                data = indata.tobytes()
                # Saat otak bekerja / menunggu suara pertama, derau ruangan
                # tidak boleh sampai ke model (VAD high bisa membatalkan
                # giliran jawaban) — TETAPI menghentikan aliran audio sama
                # sekali membuat model proaktif memilih diam (terlihat di log
                # 23 Okt: "mulut tidak bersuara" 3x berturut). Jadi yang
                # dikirim adalah SENYAP: derau tertahan, aliran tetap hidup.
                if not _mic_stream_allowed(
                        muted=False, phone_active=False,
                        in_brain=getattr(self, "_in_brain", False),
                        awaiting_reply=getattr(self, "_awaiting_reply", False)):
                    data = b"\x00" * len(data)
                loop.call_soon_threadsafe(
                    _put_drop_oldest,
                    self.out_queue,
                    {"data": data, "mime_type": "audio/pcm"}
                )
                # Feed the live mic level to the HUD so the waveform reacts to
                # the user's actual voice while listening. Purely cosmetic — any
                # failure here must never disturb the mic.
                try:
                    self.ui.set_audio_level(_pcm_level(indata))
                except Exception:
                    pass

        try:
            def _open_mic(dev):
                return sd.InputStream(
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    device=dev,
                    callback=callback,
                )

            # Which microphone. resolve() returns None for "system default" and
            # for a saved device that is no longer present — so a headset
            # unplugged since the last run falls back to the built-in mic
            # instead of raising on startup and taking the session with it.
            _mic_name = get_input_device()
            _mic_dev  = audio_devices.resolve(_mic_name, "input")
            if _mic_dev is not None:
                print(f"[JARVIS] 🎤 Input device: {_mic_name}")
            try:
                _mic_stream = _open_mic(_mic_dev)
            except Exception as _e:
                # A device the picker listed but the driver will not open right
                # now — exclusive mode, a webcam already in use, a virtual mic
                # whose source went away. Chosen hardware failing must never
                # mean the assistant cannot hear at all.
                if _mic_dev is None:
                    raise
                print(f"[JARVIS] ⚠️  Mic '{_mic_name}' failed: {_e} — using default")
                self.ui.write_log(
                    f"SYS: Microphone '{_mic_name}' unavailable — using system default."
                )
                _mic_stream = _open_mic(None)

            with _mic_stream:
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _flush_pending_vision(self) -> bool:
        """Send a captured frame immediately after its tool response.

        The frame is already in hand by the time `screen_process` returns — the
        capture happened inside the tool call. The old flow still made the model
        speak a turn first and only injected the image on that turn's
        turn_complete, which cost a whole extra round trip AND produced two
        spoken answers: one improvised without the picture, then the real one.
        Sending it here means the model has the tool result and the image before
        it generates anything, so the user gets one answer, sooner.
        """
        if not (self._pending_vision and self.session):
            return False

        import base64 as _b64
        img_b, mime_t, question, angle = self._pending_vision
        self._pending_vision = None
        b64 = _b64.b64encode(img_b).decode("ascii")
        print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")

        # Label the source. Without it the image arrives carrying nothing but
        # the user's own sentence, and a screenshot of this app — which has a
        # face in the middle of it — got read as a photo of the user. What the
        # label *means* is explained once, in the generated [SELF] block.
        src = ("[IMAGE SOURCE: WEBCAM]" if angle == "camera"
               else "[IMAGE SOURCE: SCREEN CAPTURE]")
        await self.session.send_client_content(
            turns={"role": "user", "parts": [
                {"inline_data": {"mime_type": mime_t, "data": b64}},
                {"text": f"{src}\n\n{question}"},
            ]},
            turn_complete=True,
        )

        if self._vision_cam_active:
            # Camera: stay busy until JARVIS has finished speaking the answer,
            # then close the preview.
            self._vision_cam_active    = False
            self._vision_close_pending = True
        else:
            self._vision_busy = False
        return True

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    # ── Session resumption ───────────────────────────────────
                    # The server sends this periodically. `resumable` goes false
                    # while a turn is mid-flight — replaying a handle from that
                    # moment is what the flag exists to prevent — so only
                    # resumable handles are kept. This is three lines and it is
                    # the entire fix for "every reconnect forgets everything".
                    _sru = getattr(response, "session_resumption_update", None)
                    if _sru is not None:
                        if getattr(_sru, "resumable", False) and getattr(_sru, "new_handle", None):
                            if self._resume_handle is None:
                                print("[JARVIS] 🔗 Session resumption armed")
                            self._resume_handle = _sru.new_handle

                    if response.data:
                        # Mulut bersuara — jawaban otak sudah tersampaikan,
                        # jaring pengaman tidak diperlukan lagi.
                        self._mouth_pending = None
                        self._awaiting_reply = False
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            self._mouth_pending = None
                            self._awaiting_reply = False
                            # A turn that involves a tool call passes through
                            # several turn_completes, and the API re-sends the
                            # tail of the transcript across them. Comparing only
                            # against the previous chunk missed that — once
                            # out_buf had been flushed and emptied, the repeat
                            # sailed straight back in, which logged the answer
                            # twice AND made the avatar mouth it twice.
                            if txt and not _is_repeat_chunk(txt, out_buf):
                                out_buf.append(txt)
                                # Hand the words to the mouth as they arrive, so
                                # the avatar can form the consonants the audio
                                # alone cannot show. Pure string work — it adds
                                # nothing measurable to the response path.
                                self._visemes.feed_text(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                # The router reads this: a new utterance
                                # starts a fresh buffer, and it is kept after
                                # turn_complete because the tool call that
                                # belongs to it arrives later.
                                if not self._utt_open:
                                    self._live_utterance = ""
                                    self._utt_open = True
                                    self._turn_tool_used = False
                                    # Fase 4 barge-in: sampaikan catatan
                                    # potongan giliran lalu SEKARANG — model
                                    # sudah mendengar ucapan ini lewat audio,
                                    # jadi catatan harus menyusul SEBELUM
                                    # turn-nya ditutup agar masuk konteks
                                    # giliran ini. turn_complete=False:
                                    # ini konteks, bukan giliran baru.
                                    await self._flush_cut_note()
                                    # Fase 4 Slice 5: hasil yang ditahan
                                    # selama jendela sunyi ikut konteks
                                    # giliran ini — satu blok, sekali.
                                    await self._flush_deferred_results()
                                self._live_utterance = (
                                    self._live_utterance + " " + txt).strip()
                                # While the screen is shared, the coaching
                                # goal follows what the user just asked —
                                # otherwise the panel keeps repeating hints
                                # for the task that started the share.
                                if (self._screen_on
                                        and len(self._live_utterance.split()) >= 3):
                                    self._screen_goal = self._live_utterance.strip()
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()
                            # The utterance stays in self._live_utterance: the
                            # tool call that answers it has not arrived yet.
                            # Only the NEXT transcription delta starts a new one.
                            self._utt_open = False
                            # Perintah ganti model ditangkap dari TRANSKRIP,
                            # bukan menunggu tool: mulut pernah menjawab
                            # "saya tidak bisa berpindah model" padahal tool
                            # switch_model ada. Deterministik, tanpa model.
                            try:
                                _vt = _vent_command(self._live_utterance)
                                if _vt and _control_once(
                                        getattr(self, "_vent_handled", ""),
                                        self._live_utterance):
                                    self._vent_handled = self._live_utterance
                                    _utt_now = self._live_utterance
                                    # Ucapan ini sudah ditangani; jangan
                                    # dibaca lagi oleh giliran berikutnya.
                                    self._live_utterance = ""
                                    await self._apply_vent_state(
                                        _vt == "on")
                                elif (_vent_ambiguous(self._live_utterance)
                                        and _control_once(
                                            getattr(self, "_vent_handled", ""),
                                            self._live_utterance)):
                                    # Zona ambigu: bentuk kalimat tidak cukup.
                                    # Model murah memutuskan dengan KONTEKS,
                                    # di luar jalur audio supaya tidak menahan
                                    # receive loop.
                                    self._vent_handled = self._live_utterance
                                    _utt_amb = self._live_utterance
                                    self._live_utterance = ""
                                    print("[JARVIS] 🧠 mode: ambigu — "
                                          "meminta putusan model")
                                    asyncio.create_task(
                                        self._classify_vent_command(_utt_amb))
                            except Exception:                # noqa: BLE001
                                pass
                            try:
                                _sw = _switch_command(self._live_utterance)
                                if (_sw and _control_once(
                                        getattr(self, "_switch_handled", ""),
                                        self._live_utterance)):
                                    self._switch_handled = self._live_utterance
                                    print(f"[JARVIS] 🔁 perintah model: {_sw}")
                                    self._live_utterance = ""
                                    asyncio.create_task(
                                        self._apply_switch_command(_sw))
                            except Exception:            # noqa: BLE001
                                pass

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                # Fase 4 barge-in: dua fakta akan hilang
                                # bersama buffer yang dibuang — apa yang
                                # JARVIS sedang katakan (out_buf) dan ucapan
                                # pengguna yang memotong (in_buf). Disimpan
                                # sebagai catatan sekali pakai supaya giliran
                                # berikutnya tahu harus MELANJUTKAN, bukan
                                # mengulang dari awal. Bisa mati lewat
                                # config/voice_flags.json (voice.bargein_note).
                                if self._flag("voice.bargein_note"):
                                    self._cut_note = compose_cut_note(
                                        " ".join(out_buf),
                                        " ".join(in_buf))
                                    # Ucapan pemotong bisa sudah mengalir
                                    # SEBELUM turn yang dipotong ini ditutup —
                                    # menyampaikannya di sini menjaga catatan
                                    # tetap menempel pada ucapan yang benar.
                                    # Kalau sesi belum ada, catatan tersimpan
                                    # dan disampaikan saat ucapan berikutnya.
                                    await self._flush_cut_note()
                                in_buf  = []
                                out_buf = []
                                self._visemes.reset()
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self._last_out_logged = ""   # new exchange
                                self.ui.write_log(f"You: {full_in}")
                                self._session_log.append(f"User: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            # Second line of defence: even if a repeat slips
                            # into a *fresh* buffer after a flush, never log the
                            # same answer (or a tail of it) twice in a row.
                            if full_out and len(full_out) >= _REPEAT_MIN and self._last_out_logged:
                                if full_out in self._last_out_logged:
                                    full_out = ""
                            if full_out:
                                self._last_out_logged = full_out
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_log.append(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            # The one failure the dispatcher cannot see: a
                            # writing request answered with WORDS instead of
                            # an action ("sudah saya kirim") while no tool ran
                            # at all. The claim is already on screen, so the
                            # brain takes over and the truth replaces it.
                            if (full_out and not self._turn_tool_used
                                    and not self._in_brain
                                    # Mode curhat: JANGAN pernah memaksa
                                    # otak, walau ceritanya memuat kata
                                    # seperti "simpan" atau "melakukan".
                                    and not getattr(self, "_vent_mode", False)
                                    and self._write_intent()
                                    # 23 Okt — dulu hanya "sudah/telah" yang
                                    # tertangkap. "Saya buatkan filenya…"
                                    # adalah klaim tindakan tanpa kata itu,
                                    # dan itulah celah "Gemini mengerjakan
                                    # tugas berat sendiri".
                                    and (claims_done(full_out)
                                         or claims_action(full_out))):
                                asyncio.create_task(self._redo_with_brain())

                            if self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        calls = list(response.tool_call.function_calls)
                        fn_responses: list = [None] * len(calls)

                        async def _run_tool(i: int, fc) -> None:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fn_responses[i] = await self._execute_tool(fc)

                        # The mouth sometimes hands the brain two utterances
                        # in one response (a greeting plus the wake word it
                        # heard). A provider call is stateless, so brain calls
                        # run together instead of queueing — one wait instead
                        # of two. Everything else keeps its original order.
                        if len(calls) > 1 and all(
                                getattr(c, "name", "") == "brain"
                                for c in calls):
                            await asyncio.gather(
                                *[_run_tool(i, fc)
                                  for i, fc in enumerate(calls)])
                        else:
                            for i, fc in enumerate(calls):
                                await _run_tool(i, fc)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
                        # Jaring pengaman: kalau mulut tidak bersuara dalam
                        # masa tenggang, aplikasi mengucapkan jawaban otak.
                        if getattr(self, "_mouth_pending", None):
                            asyncio.create_task(self._mouth_watchdog())
                        # Fase 4 Slice 2: tanda terima milik kode baru
                        # diucapkan setelah respons tool aman terkirim.
                        await self._flush_receipts()
                        await self._flush_pending_vision()
        except Exception as e:
            if _is_conn_closed(e):
                # 23 Okt — socket Live mati (keepalive timeout / GoAway 1008).
                # Dulu ini melempar ExceptionGroup sampai crash. Sekarang:
                # sebutkan SEBABNYA (jangan generik, supaya bisa didiagnosis),
                # lalu minta sambung ulang — bukan diam menunggu keajaiban.
                # 24 Okt — APIError 1008 kini dikenali `_is_conn_closed`, jadi
                # kondisi yang memang dirancang ini TIDAK lagi mencetak
                # traceback 25 baris setiap kali terjadi.
                _why = " ".join(str(e).split()) or type(e).__name__
                _kind = ("batas durasi sesi" if _is_session_limit(e)
                         else "koneksi terputus")
                print(f"[JARVIS] {_kind.capitalize()} — menyambung ulang. "
                      f"({type(e).__name__}: {_why[:110]})")
                try:
                    self.request_reconnect(keep_context=True,
                                           reason="connection lost")
                except Exception:                    # noqa: BLE001
                    pass
                return
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        _spk_name = get_output_device()
        _spk_dev  = audio_devices.resolve(_spk_name, "output")
        if _spk_dev is not None:
            print(f"[JARVIS] 🔊 Output device: {_spk_name}")

        def _open_spk(dev):
            st = sd.RawOutputStream(
                samplerate=RECEIVE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                device=dev,
            )
            st.start()
            return st

        try:
            stream = _open_spk(_spk_dev)
        except Exception as _e:
            # A chosen output that the host API accepts by name but refuses to
            # open (exclusive mode, wrong sample rate, device asleep) must not
            # cost the user their voice. Fall back to the default and say so.
            if _spk_dev is None:
                raise
            print(f"[JARVIS] ⚠️  Output device '{_spk_name}' failed: {_e} — using default")
            self.ui.write_log(f"SYS: Speaker '{_spk_name}' unavailable — using system default.")
            stream = _open_spk(None)

        # Ask the device how far behind the speakers actually are, rather than
        # assuming. This is what the echo tail is sized from, so a machine with a
        # large audio buffer gets a correspondingly longer guard — and one with a
        # tiny buffer is not penalised with a delay it does not need.
        try:
            lat = float(getattr(stream, "latency", 0.0) or 0.0)
            if 0.0 < lat < 1.0:
                self._out_latency = lat
            print(f"[JARVIS] 🔊 Output latency {self._out_latency*1000:.0f} ms "
                  f"→ echo tail {(self._out_latency + _TAIL_MARGIN)*1000:.0f} ms")
        except Exception:
            pass

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue

                self.set_speaking(True)

                # Batch all immediately-available chunks into one write to reduce
                # thread-pool round-trips (was one asyncio.to_thread per 50ms slice).
                # Cap at ~200 ms so interrupt() still stops audio within ~200 ms.
                batch = bytearray(chunk)
                while len(batch) < 9600:   # 9600 bytes ≈ 200 ms at 24 kHz / 16-bit mono
                    try:
                        batch.extend(self.audio_in_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # Drive the HUD waveform and the avatar's mouth from JARVIS's
                # own voice. The batch is up to 200 ms long, so we hand over a
                # *schedule* of 20 ms viseme frames instead of a single averaged
                # level and let the HUD play it out in step with the audio.
                try:
                    pcm = np.frombuffer(bytes(batch), dtype=np.int16)
                    hop = _VIS_HOP / RECEIVE_SAMPLE_RATE
                    frames = _pcm_visemes(pcm, sr=RECEIVE_SAMPLE_RATE)
                    # When does this batch become audible? The stream was
                    # started at launch and its callback has been pulling
                    # silence ever since, so the first bytes of a reply reach
                    # the speaker about one callback period later — NOT one
                    # buffer later. `stream.latency` reports the buffer's
                    # capacity, which is how much can be queued ahead, and on
                    # Windows that is commonly 300-500 ms. Anchoring on it put
                    # the entire schedule a buffer late; that is the half second
                    # of lag, and it grew with whatever the device reported.
                    #
                    # After the anchor nothing needs measuring: the device
                    # consumes at exactly realtime, so each batch sounds one
                    # batch-duration after the one before it. The cursor is
                    # re-anchored only when it leaves the range physically
                    # possible — behind `now` means the device drained and this
                    # batch starts a fresh stretch of speech, while further
                    # ahead than the buffer can hold means it has drifted.
                    now = time.time()
                    horizon = self._out_latency + _CURSOR_SLACK
                    if not (now <= self._play_cursor <= now + horizon):
                        self._play_cursor = now + _FIRST_SOUND
                    at = self._play_cursor
                    # Advance by the batch's own duration whether or not it
                    # yielded frames, so a block too short to analyse cannot
                    # shift everything after it out of step with the audio.
                    self._play_cursor += pcm.size / RECEIVE_SAMPLE_RATE
                    if frames:
                        frames = self._visemes.frames(frames, hop)
                        self.ui.push_visemes(frames, hop, at)
                        # Barge-in needs to know what we are playing, not just
                        # how loud: the guard subtracts this from the microphone.
                        self._out_level = max(f[0] for f in frames)
                        self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE,
                                               self._out_level)
                    else:
                        lvl = _pcm_level(pcm)
                        self.ui.set_audio_level(lvl)
                        self._out_level = lvl
                        self._echo.note_output(pcm, RECEIVE_SAMPLE_RATE, lvl)
                except Exception:
                    pass

                try:
                    await asyncio.to_thread(stream.write, bytes(batch))
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        # The briefing fires before the user has said anything, so the
        # remembered language is the only signal there is. It is a starting
        # point, not a setting: the moment they reply, their language wins.
        lang_clause = (f" Speak this greeting in {lang}, then follow the "
                       f"user's own language from their first reply onward."
                       if lang else "")
        name_clause = f" Address the user as {name}." if name else ""

        # Inject last session context if available — pop removes it so it's never repeated
        last = await asyncio.to_thread(pop_last_session)
        session_clause = ""
        if last:
            try:
                _delta = (datetime.now() - datetime.strptime(last["date"], "%Y-%m-%d")).days
                _when  = "earlier today" if _delta == 0 else ("yesterday" if _delta == 1 else f"{_delta} days ago")
            except Exception:
                _when = "last time"
            session_clause = (
                f" Also briefly and naturally mention that {_when}: {last['summary']}"
            )

        p1 = (
            f"Greet the user warmly, mention it is {time_str}, and say you are fetching today's news now.{session_clause} "
            f"Keep it to 2 short sentences max. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"role": "user", "parts": [{"text": p1}]},
            turn_complete=True,
        )
        print("[JARVIS] Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = (f" Speak in {lang} unless the user has since "
                            f"spoken another language, in which case use theirs."
                            if lang else "")

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # Extra buffer: turn_complete fires when Gemini finishes *generating*
                # Phase 1, but audio may still be playing.  Waiting a beat here
                # prevents Phase 2 audio from arriving while Phase 1 is mid-sentence
                # (which sounds like a "repeated first response" to the user).
                if turn_waited:
                    await asyncio.sleep(0.8)
                else:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=8.0)
                except Exception as e:
                    self.ui.write_log(f"SYS: News fetch timed out/failed: {e!r}")
                    news_text = ""

                if not self.session:
                    return

                failed = (not news_text) or news_text.startswith(
                    ("No news found", "Search failed", "Please provide")
                )
                if not failed:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    self.ui.write_log(
                        f"SYS: News unavailable — backend returned: {news_text[:120]!r}"
                    )
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": p2}]},
                    turn_complete=True,
                )
                print("[JARVIS] Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                print(f"[JARVIS] Briefing phase 2 failed: {e}")
                self.ui.write_log("SYS: Could not fetch the news for the briefing.")

        asyncio.create_task(_deliver_news())

    # ── Session memory ──────────────────────────────────────────────────────────

    async def _save_session_summary(self) -> None:
        """Summarise the current session in 1-2 sentences and save to long_term.json."""
        log = self._session_log
        if len(log) < 3:          # need at least one exchange to be worth saving
            return
        self._session_log = []    # reset immediately so the next session starts clean

        memory = load_memory()
        lang_entry = memory.get("identity", {}).get("language", {})
        lang = (lang_entry.get("value", "") if isinstance(lang_entry, dict) else str(lang_entry)).strip()
        lang = lang or "English"

        convo = "\n".join(log[-40:])   # cap at last 40 turns to stay within token budget
        prompt = (
            f"Summarize this conversation in 1-2 sentences in {lang}. "
            "Focus on what the user accomplished or discussed. "
            "Output ONLY the summary text, nothing else:\n\n" + convo
        )
        try:
            from core import gemini
            summary = await asyncio.to_thread(
                gemini.text, prompt, gemini.SMART, None, 30_000,
            )
            if summary:
                save_session_summary(summary, lang)
            else:
                _fb = _fallback_summary(log)
                if _fb:
                    save_session_summary(_fb, lang)
                    print("[Memory] ⚠️ ringkasan model kosong — "
                          "dipakai ucapan terakhir pengguna")
        except Exception as e:
            print(f"[Memory] ⚠️ Session summary failed: {e}")
            # 24 Okt — Gemini bisa kehabisan kuota (log: "out of quota"),
            # dan tanpa cadangan seluruh sesi curhat HILANG tanpa jejak.
            # Menyimpan ucapan terakhir apa adanya masih bisa ditemukan
            # `recall_memory`; kehilangan total tidak bisa.
            _fb = _fallback_summary(log)
            if _fb:
                save_session_summary(_fb, lang)
                print("[Memory] 💾 ringkasan cadangan disimpan "
                      "(ucapan terakhir pengguna)")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if not alert or not self.session or not self._awake:
                continue
            # Don't interrupt an active conversation
            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking or (time.monotonic() - self._last_user_speech) < 10:
                continue
            try:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": alert}]},
                    turn_complete=True,
                )
            except Exception as e:
                print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Background monitor ──────────────────────────────────────────────────────

    async def _run_background_monitor(self) -> None:
        """Check user-configured topics once per day; speak alerts when new headlines appear."""
        await asyncio.sleep(300)          # wait 5 min after startup before first check
        while True:
            if self.session and self._awake:
                # Don't interrupt if user spoke recently or JARVIS is mid-sentence
                with self._speaking_lock:
                    speaking = self._is_speaking
                recent_speech = (time.monotonic() - self._last_user_speech) < 30
                if not speaking and not recent_speech:
                    try:
                        alerts = await asyncio.to_thread(monitor_check_all)
                        memory = load_memory()
                        lang_e = memory.get("identity", {}).get("language", {})
                        lang   = (lang_e.get("value", "") if isinstance(lang_e, dict) else str(lang_e)).strip() or "English"
                        for alert in alerts:
                            msg = (
                                f"{alert}\n\n"
                                f"Inform the user about this development naturally in {lang}. "
                                "One brief sentence only."
                            )
                            await self.session.send_client_content(
                                turns={"role": "user", "parts": [{"text": msg}]},
                                turn_complete=True,
                            )
                            print("[JARVIS] Monitor alert sent.")
                            await asyncio.sleep(6)   # gap between consecutive alerts
                    except Exception as e:
                        print(f"[Monitor] ⚠️ Background check error: {e}")
            await asyncio.sleep(1800)     # check every 30 minutes

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session or not self._awake:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            # ── Gerbang diam (Kandidat C) ─────────────────────────────────
            # Rencana deterministik SEBELUM meminta Gemini berbicara: kalau
            # tak ada yang menuntut jawaban, cek-in dibatalkan di sini —
            # bukan dibiarkan diputuskan model setelah prompt panjang sudah
            # terkirim. Sakelar voice.silence_gate OFF = perilaku lama.
            if self._flag("voice.silence_gate"):
                _diam, _alasan = stay_silent_plan(
                    they_just_talked=not self._quiet_window_passed(),
                    news_owed=bool(self._deferred_results),
                    receipt_owed=bool(self._receipt_pending),
                    already_spoke=self._last_spoke > self._last_user_speech,
                )
                if _diam:
                    print(f"[JARVIS] 🤫 Diam: {_alasan}")
                    continue
                # WAJIB bicara. Fakta tertunggak diucapkan oleh APLIKASI
                # sendiri sebagai fakta berbatas (blok [HASIL_TUNDA] /
                # tanda terima), bukan dikarang ulang oleh model.
                if self._deferred_results:
                    await self._flush_deferred_results()
                    continue
                if self._receipt_pending:
                    await self._flush_receipts()
                    continue
                # they_just_talked — tak mungkin dengan should_trigger
                # (butuh pengguna diam ≥900 s), tapi kalau ambang sunyi
                # suatu hari dikecilkan: percakapan sedang hidup, giliran
                # jawab yang memegang ucapan. Menyelipkan cek-in di sini
                # bicara ganda.
                print("[JARVIS] 🤫 Cek-in dilewati — percakapan masih hidup.")
                continue

            self._proactive.mark_triggered()

            try:
                memory       = await asyncio.to_thread(load_memory)
                monitors     = await asyncio.to_thread(list_monitors)
                recent_turns = self._session_log[-8:] if self._session_log else []
                prompt = self._proactive.build_prompt(
                    memory       = memory,
                    monitors     = monitors or None,
                    recent_turns = recent_turns or None,
                )
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                print("[JARVIS] Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    # A remote command is deliberate control and the phone user
                    # has no desktop WAKE button — so it wakes JARVIS if asleep.
                    if self._wake_enabled and not self._awake:
                        self.wake(reason="remote command")
                    await self.session.send_client_content(
                        turns={"role": "user", "parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    # ── who is answering (live badge + spoken fallback notice) ───────────────

    def _on_brain_line(self, line: str) -> None:
        """Called from whatever thread answered. Moves the header badge to the
        model that actually produced the last answer."""
        try:
            lb = _gemini.last_brain()
            kind = lb.get("kind")
            if kind == "provider":
                label = f"{lb.get('model', '?')} ({lb.get('pid', '')})"
                self.ui.set_model(label)
                self.ui.set_services(brain=lb.get("model", "?"))
            elif kind == "gemini":
                m = str(lb.get("model", "")).replace("models/", "")
                label = "gemini live" if m == "live" else m
                self.ui.set_model(label)
                self.ui.set_services(brain=label)
        except Exception:                      # noqa: BLE001
            pass

    async def _apply_vent_state(self, want: bool) -> None:
        """Pasang status mode curhat — satu tempat, idempoten."""
        was = self._vent_mode
        self._vent_mode = bool(want)
        if bool(want) == bool(was):
            return                        # sudah di status itu: jangan bicara
        _msg = ("Mode curhat AKTIF — saya yang menjawab, otak tidak dipanggil."
                if want else
                "Mode curhat selesai — kembali normal.")
        print(f"[JARVIS] 🫂 {_msg}")
        self.ui.write_log(f"SYS: {_msg}")
        if want:
            # Konteks curhat dikirim dan DITUNGGU selesai SEBELUM konfirmasi
            # diucapkan. Dulu memakai create_task, sehingga urutannya tidak
            # dijamin — model bisa menjawab lebih dulu tanpa konteks.
            try:
                await asyncio.wait_for(self._inject_vent_context(), timeout=5.0)
            except Exception:                    # noqa: BLE001
                pass
        try:
            self.plugin_say("Katakan SATU kalimat singkat ke pengguna, "
                            "bahasa yang sama: " + _msg)
        except Exception:                        # noqa: BLE001
            pass

    async def _classify_vent_command(self, utterance: str) -> None:
        """Putusan KONTEKS untuk zona ambigu: perintah atau bahasan.

        Dipanggil hanya bila bentuk kalimat tidak cukup. Memakai tier murah
        (`gemini.FAST`) dan konteks dua giliran terakhir. Gagal / jawaban
        ngawur -> TIDAK melakukan apa pun: lebih baik satu perintah tidak
        dijalankan daripada mode berganti tanpa diminta.
        """
        try:
            from core import gemini
            ctx = " | ".join(self._session_log[-2:]) if self._session_log else ""
            prompt = (
                "Tugas: tentukan apakah UCAPAN pengguna adalah PERINTAH "
                "mengubah mode (mode curhat / mode cerita), atau hanya "
                "MEMBAHAS mode itu.\n"
                "Konteks percakapan terakhir: " + (ctx[:400] or "(kosong)") + "\n"
                'Ucapan: "' + str(utterance)[:300] + '"\n'
                "Jawab SATU kata: PERINTAH atau BAHAS."
            )
            raw = await asyncio.to_thread(
                gemini.text, prompt, gemini.FAST, None, 6000)
            verdict = _parse_verdict(raw)
            print(f"[JARVIS] 🧠 putusan mode: {verdict} "
                  f"({str(raw or '')[:40]!r})")
            if verdict != "perintah":
                return
            # Putusan bisa datang 3-4 detik kemudian (provider cadangan).
            # Kalau pengguna sudah bicara lagi, keputusan ini basi — membuang
            # lebih baik daripada mengubah mode di tengah kalimat baru.
            _now_utt = " ".join(str(self._live_utterance or "").split())
            if _now_utt and _now_utt != " ".join(str(utterance).split()):
                print("[JARVIS] putusan mode dibuang — pengguna sudah "
                      "bicara lagi")
                return
            if _VENT_OFF.search(utterance):
                await self._apply_vent_state(False)
            elif _VENT_ON.search(utterance) or re.search(
                    r"\bcurhat\b", utterance, re.IGNORECASE):
                await self._apply_vent_state(True)
        except Exception as e:                   # noqa: BLE001
            print(f"[JARVIS] putusan mode gagal (diam): {e}")

    async def _inject_vent_context(self) -> None:
        """Suntik ringkasan curhat sebelumnya ke sesi Live.

        `turn_complete=False`: ini konteks, bukan giliran — model tidak
        menjawab dan tidak bersuara karena menerimanya.
        """
        if not self.session:
            return
        try:
            text = _vent_context_text(load_memory().get("sessions") or [])
        except Exception:                        # noqa: BLE001
            text = ""
        if not text:
            return
        try:
            await self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=False,
            )
            print("[JARVIS] 🫂 konteks curhat sebelumnya disuntikkan "
                  f"({text.count('- ')} ringkasan)")
        except Exception as e:                   # noqa: BLE001
            print(f"[JARVIS] konteks curhat gagal disuntik: {e}")

    async def _apply_switch_command(self, cmd: dict) -> None:
        """Jalankan switch_model dari perintah suara — tanpa lewat otak."""
        try:
            fc = types.FunctionCall(
                name="switch_model",
                args={"model": cmd.get("model", ""),
                      "provider": cmd.get("provider", "")})
            res = await self._execute_tool(fc, _internal=True)
            said = str((getattr(res, "response", None) or {}).get("result") or "")
            self.ui.write_log(f"SYS: {said[:160]}")
            self.plugin_say("Katakan SATU kalimat singkat ke pengguna, bahasa "
                            "yang sama: " + said[:200])
        except Exception as e:                       # noqa: BLE001
            print(f"[JARVIS] perintah model gagal: {e}")

    def _on_brain_fallback(self, pid: str, model: str, err: str) -> None:
        """Provider cadangan yang menjawab — pindahkan tampilan ke kenyataan.

        23 Okt: badge tetap menampilkan model pilihan sementara provider lain
        yang menjawab, sehingga pengguna menyimpulkan rotasinya bohong. Satu
        baris log + badge yang ikut berpindah membuat keduanya konsisten.
        """
        try:
            label = f"{model} ({pid})" if model else pid
            self.ui.set_model(label)
            self.ui.set_services(brain=model or pid)
            short = " ".join(str(err).split())[:70]
            self.ui.write_log(f"SYS: otak → {pid} · {model or '?'} "
                              f"(cadangan; {short})")
            # 24 Okt — bos minta TAHU setiap kali otak bermasalah. Ini
            # diucapkan MULUT (Gemini) langsung, tanpa memanggil otak:
            # biayanya nol token provider otak.
            if _provider_notice_enabled():
                _now = time.monotonic()
                if _now - float(getattr(self, "_fb_notice_at", 0.0)) > 60.0:
                    self._fb_notice_at = _now
                    _why = ("kuota/limit" if "429" in str(err)
                            else "gangguan provider" if "503" in str(err)
                            else "error")
                    self.plugin_say(
                        "Katakan SATU kalimat singkat ke pengguna, bahasa "
                        f"yang sama: model otak {pid} sedang {_why}, jadi "
                        f"jawaban ini dari {model or pid}. Jangan menambah "
                        "penjelasan lain, jangan memanggil tool.")
        except Exception:                        # noqa: BLE001
            pass

    def _on_provider_failure(self, pid: str, err: str) -> None:
        """A custom provider failed mid-turn. Log it, move the badge, and say
        it out loud — throttled, so a provider that is down does not repeat
        the sentence on every call."""
        import time as _t
        now = _t.monotonic()
        last = getattr(self, "_provider_fail_last", 0.0)
        if now - last < 60.0:
            return
        self._provider_fail_last = now

        reason = " ".join(str(err).split())[:120]
        # 23 Okt — kalimat lama menyebut "Model custom '<pid>' gagal — Gemini
        # menjawab" seolah pid itu model pilihan pengguna. Yang gagal adalah
        # SATU TANGGA tugas berat (bisa river, bisa apa saja), sementara
        # percakapan tetap di otak custom. Badge tidak lagi dipaksa ke
        # "gemini (cadangan)": badge mengikuti model yang benar-benar
        # menjawab, lewat _on_brain_fallback / _on_brain_line.
        self.ui.write_log(f"SYS: tugas berat: '{pid}' gagal — dilanjutkan "
                          f"Gemini. ({reason[:60]})")
        if not self.session or not _provider_notice_enabled():
            # Bawaan: TIDAK mengucapkan apa pun. Pengumuman itu memicu satu
            # giliran otak tambahan (~21.000 token) hanya untuk memberi tahu
            # hal yang sudah tertulis di log dan badge.
            return
        note = ("Katakan dengan singkat ke user, satu kalimat, bahasa yang sama "
                f"dengan percakapan: tugas berat tadi dikerjakan Gemini karena "
                f"model '{pid}' bermasalah (alasan singkat: {reason}). "
                "Percakapan tetap memakai otak custom yang dipilih user.")

        async def _say():
            try:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": note}]},
                    turn_complete=True,
                )
            except Exception as e:             # noqa: BLE001
                print(f"[ProviderFail] {e}")

        try:
            asyncio.run_coroutine_threadsafe(_say(), self._loop)
        except Exception as e:                 # noqa: BLE001
            print(f"[ProviderFail] {e}")

    async def run(self):
        self._loop = asyncio.get_event_loop()
        self._reconnect_event = asyncio.Event()

        # Heavy work runs here, outside the session: a reconnect must not be
        # able to cancel a project that is half written, and the microphone
        # must stay free while it builds.
        self._jobs = JobRunner(on_start=self._on_job_start,
                               on_done=self._on_job_done,
                               on_progress=self.ui.task_progress,
                               persist_path=Path(__file__).resolve().parent
                               / "memory" / "jobs_state.json")
        # Nama tool yang benar-benar ada di registri. Dipakai kebijakan izin:
        # tool terdaftar tidak boleh diperlakukan seperti tool asing.
        try:
            self._known_tools = (set(self._action_registry.names())
                                 | {d["name"] for d in
                                    self._plugin_registry.get_tool_declarations()}
                                 | {d["name"] for d in
                                    self._mcp.get_tool_declarations()})
        except Exception:
            self._known_tools = set()

        # The SHARE chip in the HUD starts/stops the coaching loop.
        try:
            self.ui.set_screen_share_handler(self._toggle_screen_share)
        except Exception:
            pass

        # Tugas agent yang belum selesai saat aplikasi ditutup. Tidak dijalankan
        # otomatis: lebih baik bertanya sekali daripada mengulang pekerjaan yang
        # mungkin sudah tidak diinginkan.
        try:
            _pend = _load_pending_agent()
            if _pend:
                self._pending_agent_state = _pend
                _steps = len((_pend.get("plan") or {}).get("steps") or [])
                print(f"[Agent] ada tugas belum selesai: "
                      f"{str(_pend.get('task'))[:60]} "
                      f"({int(_pend.get('index') or 0)}/{_steps} langkah, "
                      f"{_pend.get('status')}) — bilang 'lanjutkan'")
        except Exception:
            pass

        # Pekerjaan non-agent (dev_agent, code_helper) yang terputus.
        try:
            _pj = self._jobs.pending() if self._jobs else []
            if _pj:
                self._pending_jobs = _pj
                print(f"[Jobs] {len(_pj)} pekerjaan terputus: "
                      + "; ".join(str(j.get("title"))[:40] for j in _pj)
                      + " — bilang 'lanjutkan'")
        except Exception:
            pass
        # One ticker for the whole process: it keeps the background job's line
        # moving (elapsed + step) even across reconnects.
        asyncio.create_task(self._run_job_ticker())

        # ── Wire the shared core services to the interface ───────────────────
        # The confirmation gate is useless without a way to ask, and a memory
        # trim is invisible without a way to say so. Both are bound once here
        # rather than passed down through every action signature.
        confirm_gate.bind(
            show = self.ui.show_confirm,
            hide = self.ui.hide_confirm,
            log  = self.ui.write_log,
        )
        set_trim_notifier(self.ui.write_log)

        # Who answered, live. The badge and the model_status tool both read
        # this record, so what the user sees is the model that actually did
        # the work on the last turn — not the one guessed at connect time.
        _gemini.set_brain_log_hook(self._on_brain_line)
        # A custom provider failing is worth SAYING: the user configured it
        # and is about to be served by Gemini instead.
        _gemini.set_provider_failure_hook(self._on_provider_failure)

        # Tell the device picker the exact rates the streams open at, from the
        # constants that actually open them — so it can never list a device that
        # cannot be opened at them.
        audio_devices.configure(SEND_SAMPLE_RATE, RECEIVE_SAMPLE_RATE)

        # Enumerate audio devices off-thread. The settings drawer must never pay
        # for host-API enumeration on the Qt thread.
        audio_devices.prefetch()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)

            async def _dashboard_serve():
                """The dashboard must never be able to kill the assistant.

                uvicorn answers a busy port with sys.exit(3); raised inside a
                task that propagates through the event loop and takes the whole
                session down — which is exactly what happened when a second
                Jarvis was still holding 8000/8001. A dashboard is a bonus,
                the voice is the product: catch BaseException, say why, and
                carry on with the dashboard switched off.
                """
                try:
                    await self._dashboard.serve()
                except SystemExit as e:
                    print(f"[Dashboard] port 8000/8001 sudah dipakai "
                          f"(SystemExit {e}) — kemungkinan instance Jarvis "
                          f"lain masih jalan. Dashboard dimatikan, Jarvis lanjut.")
                    self._dashboard = None
                except BaseException as e:            # noqa: BLE001
                    print(f"[Dashboard] berhenti: {type(e).__name__}: {e} — "
                          f"Jarvis lanjut tanpa dashboard.")
                    self._dashboard = None

            asyncio.create_task(_dashboard_serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            try:
                # Backoff abort beruntun (24 Okt). Sesi yang hidup < 20 detik
                # lalu putus = gagal cepat; dua kali berturut-turut berarti
                # server masih menolak. Menyambung ulang tanpa jeda hanya
                # memutar loop (log: 4× dalam hitungan detik).
                _prev_at = float(getattr(self, "_last_attempt_at", 0.0) or 0.0)
                if _prev_at:
                    _lived = time.monotonic() - _prev_at
                    self._fast_aborts = (
                        int(getattr(self, "_fast_aborts", 0)) + 1
                        if _lived < _FAST_ABORT_WINDOW else 0)
                _delay = _abort_backoff(getattr(self, "_fast_aborts", 0))
                if _delay > 0:
                    print(f"[JARVIS] ⏳ abort beruntun {self._fast_aborts}× — "
                          f"jeda {_delay:.0f} s sebelum menyambung ulang.")
                    self.ui.write_log(
                        f"SYS: Koneksi putus berulang — mencoba lagi dalam "
                        f"{_delay:.0f} detik.")
                    await asyncio.sleep(_delay)
                self._last_attempt_at = time.monotonic()
                print("[JARVIS] Connecting...")
                self.ui.set_state("THINKING")
                # Pick the rung to open the conversation on. A model resting
                # off a quota limit is skipped; the name is published back to
                # LIVE_MODEL so plugins follow whatever is actually in use.
                global LIVE_MODEL
                LIVE_MODEL = _gemini.live_model()
                live_model = LIVE_MODEL
                # The engine is read per connect, so flipping voice_brain in
                # the config takes effect on the next reconnect, not only
                # after a restart.
                self._brain_mode = self._read_brain_mode()
                self._brain_mcp = self._read_brain_flag("brain_mcp_tools",
                                                        False)
                if self._brain_mode:
                    print("[JARVIS] 🧠 Engine: hibrida — otak = custom API, "
                          "Gemini = telinga & suara"
                          + ("" if self._brain_mcp else
                             " (tool MCP on-demand)"))
                print(f"[JARVIS] Live model: {live_model}")
                # Publish it to the header badge. Di mode hibrida yang
                # menjawab adalah OTAK, bukan Gemini — dan provider yang
                # terakhir berhasil, bukan yang terpilih (24 Okt: GLM yang
                # menjawab, badge tetap "claude").
                try:
                    _pid = get_active_provider() or ""
                    _pcfg = get_provider_config(_pid) if _pid else {}
                    _llm = getattr(getattr(self, "_brain", None), "llm", None)
                    _last = str(getattr(_llm, "last", "") or "")
                    _h = _llm.health() if hasattr(_llm, "health") else []
                    _label = _badge_label(bool(self._brain_mode), _pid,
                                          str(_pcfg.get("model") or ""),
                                          _last, _h)
                except Exception:                    # noqa: BLE001
                    _label = live_model
                self.ui.set_model(_label)
                # The status board's service rows: what this run actually
                # loaded, so "MCP 207 tool" is a fact rather than a label.
                try:
                    self.ui.set_services(
                        mcp_tools=len(self._mcp.get_tool_declarations()),
                        plugins=len(self._plugin_registry.get_tool_declarations()),
                        brain=live_model.replace("models/", ""),
                        wake="AKTIF" if self._wake_enabled else "OFF")
                except Exception as _e:            # noqa: BLE001
                    print(f"[Status] services: {_e}")

                _resumed_with = self._resume_handle is not None
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                # v1alpha carries proactive audio; if it gets rejected we fall
                # back to v1beta.
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1alpha" if self._enhanced_live else "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=live_model, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._live_down       = False   # sesi baru hidup lagi
                    self._mouth_pending   = None
                    self._awaiting_reply  = False
                    self._session_started = time.monotonic()
                    self._rotating        = False
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    print("[JARVIS] Connected.")
                    if _resumed_with:
                        # Say it plainly: the difference between "it reconnected"
                        # and "it reconnected and still knows what we were doing"
                        # is the whole point, and it is invisible otherwise.
                        self.ui.write_log("SYS: Reconnected — conversation restored.")

                    # Wake word: a FRESH launch comes up ACTIVE (mic live,
                    # listening) — never asleep at open, because the user asked
                    # to be able to talk immediately without calling it first.
                    # The wake word still does its job after that: after
                    # WAKE_SLEEP_TIMEOUT of silence JARVIS sleeps and "Hey
                    # Jarvis" (or the UI wake button) brings it back.
                    # A reconnect is a different thing — the conversation was
                    # in progress when the connection dropped, so the
                    # assistant comes back exactly as awake or asleep as it was.
                    # Forcing sleep here is what made a dropped packet, or the
                    # API's own idle timeout, silently mute a mid-conversation
                    # JARVIS: the user speaks, nothing is streamed because the
                    # mic is gated, and the only way back is the wake word again.
                    if self._wake_enabled:
                        self._ensure_wake_detector()
                        if not self._ever_connected:
                            self._awake = True
                            self.ui.set_state("LISTENING")
                            self.ui.write_log("SYS: JARVIS online.")
                        elif self._awake:
                            self.ui.set_state("LISTENING")
                            self.ui.write_log("SYS: Reconnected — conversation continues.")
                        else:
                            self.ui.set_state("SLEEPING")
                            self.ui.write_log("SYS: Reconnected — still asleep. Say 'Hey Jarvis' to wake me.")
                    else:
                        self._awake = True
                        self.ui.set_state("LISTENING")
                        self.ui.write_log("SYS: JARVIS online.")
                    self._ever_connected = True

                    # Results that finished while no session existed are
                    # spoken now, on the first turn of the new session.
                    if self._pending_job_msgs:
                        self._flush_pending_job_msgs()

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    self._reconnect_event.clear()  # ignore requests from before this session
                    tg.create_task(self._watch_reconnect())
                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_background_monitor())
                    tg.create_task(self._run_proactive_mode())
                    tg.create_task(self._run_sleep_watch())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch (if enabled).
                    # Guarded by _awake so a session that comes up asleep (only
                    # possible on a reconnect now) never talks until woken.
                    if not self._briefing_sent and get_brief_enabled() and self._awake:
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                # Voluntary reconnect (voice change) — not an error. Rebuild the
                # session immediately with no backoff and no scary logs.
                if _is_reconnect_signal(e):
                    print("[JARVIS] Voluntary reconnect requested.")
                    if not _keep_context_of(e):
                        # A deliberate clean slate (voice change) — drop the
                        # handle so the next connect really does start empty.
                        self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                # The session reached the API's duration limit — the server
                # asked us to reconnect and the socket was still open. Not a
                # failure: reconnect immediately with the resumption handle so
                # the conversation continues, and say so calmly instead of
                # printing a traceback for a condition that is by design.
                if _is_session_limit(e):
                    print("[JARVIS] Session reached its time limit — reconnecting.")
                    self.ui.write_log(
                        "SYS: Session reached its time limit — reconnecting.")
                    self._conn_backoff = 0
                    continue

                # A resumption handle the server will not accept — expired, or
                # belonging to a session it has since dropped. Without this, the
                # same dead handle would be replayed on every retry and the
                # assistant would never come back at all: the feature meant to
                # survive a reconnect would be the thing preventing one. Drop it
                # once and let the next attempt start clean.
                if _resumed_with and (
                    "resum" in str(e).lower()
                    or "handle" in str(e).lower()
                    or "INVALID_ARGUMENT" in str(e)
                    or "NOT_FOUND" in str(e)
                ):
                    print("[JARVIS] 🔗 Resumption handle rejected — starting a fresh session")
                    self.ui.write_log("SYS: Could not restore the conversation — starting fresh.")
                    self._resume_handle = None
                    self._conn_backoff = 0
                    continue

                err_str = str(e)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Out of quota, or this model is not available to this key —
                # step down the ladder and reconnect straight away. This is the
                # difference between "JARVIS is quieter today" and "JARVIS does
                # not start today": one model means one daily limit, and the
                # limit always arrives mid-conversation.
                if _gemini.note_live_failure(live_model, err_str):
                    nxt = _gemini.live_model()
                    self.ui.write_log(
                        f"SYS: Switching to {nxt.split('/')[-1]} — the previous "
                        f"model is out of quota."
                        if nxt != live_model else
                        "SYS: Every live model is rate-limited — retrying.")
                    self._conn_backoff = 0 if nxt != live_model else 15
                    if nxt == live_model:
                        await asyncio.sleep(self._conn_backoff)
                    continue

                # Turn-taking / media / thinking knobs rejected by the server
                # (preview API drift) — drop them first, because they are the
                # newest fields and the cheapest to lose. Proactive audio is
                # tried again on the next pass if the error persists.
                if self._tuned_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                    or "realtime_input" in err_str.lower()
                    or "media_resolution" in err_str.lower()
                    or "thinking" in err_str.lower()
                ):
                    self._tuned_live = False
                    print("[JARVIS] Live tuning rejected — reconnecting without it.")
                    continue

                # Proactive audio rejected by the server (preview API drift) —
                # drop it and reconnect with the plain config.
                if self._enhanced_live and (
                    "INVALID_ARGUMENT" in err_str
                    or "proactiv" in err_str.lower()
                    or "Unknown name" in err_str
                    or "unexpected keyword" in err_str
                ):
                    self._enhanced_live = False
                    self.ui.write_log(
                        "SYS: Proactive audio unavailable — reconnecting without it."
                    )
                    continue

                # Invalid API key — stop hammering the API, prompt re-configuration
                if "API key not valid" in err_str or "1007" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    self._conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Connection failed — retrying in {_conn_backoff}s. "
                        "(a VPN may be required)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None
                # Only save if there was a real conversation (≥3 turns)
                if len(self._session_log) >= 3:
                    _t = asyncio.create_task(self._save_session_summary())
                    self._bg_tasks.add(_t)
                    _t.add_done_callback(self._bg_tasks.discard)

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            # Jalur ini sudah punya jeda sendiri (_conn_backoff 3–60 s), jadi
            # jangan ditambah backoff abort beruntun.
            self._last_attempt_at = 0.0
            self._fast_aborts = 0
            delay = getattr(self, "_conn_backoff", 3)
            print(f"[JARVIS] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

_LOCK_SOCK = None


def _single_instance_guard(port: int = 8019) -> None:
    """Tolak instance kedua: dua JARVIS berbagi mic, port, dan Telegram —
    hasilnya echo (JARVIS mendengar instance lain), balasan dobel, dan
    dashboard mati karena port bentrok."""
    global _LOCK_SOCK
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
    except OSError:
        try:
            s.close()
        except Exception:                        # noqa: BLE001
            pass
        print("[JARVIS] Sudah ada instance JARVIS yang berjalan — instance "
              "ini berhenti.")
        print("[JARVIS] Tutup JARVIS yang lama dulu, lalu jalankan lagi.")
        raise SystemExit(0)
    _LOCK_SOCK = s


# Berapa lama menunggu suara mulut sebelum jawaban otak diucapkan oleh
# aplikasi sendiri. 6 detik cukup untuk jawaban yang benar-benar datang
# (probe: audio pertama ~1-2 s) tanpa membuat pengguna menunggu diam.
_MOUTH_GRACE_S = 6.0


def _mic_stream_allowed(*, muted: bool, phone_active: bool,
                        in_brain: bool, awaiting_reply: bool) -> bool:
    """Bolehkah frame mic dikirim ke model SEKARANG?

    Dua jendela ditutup dan itu bukan penghematan bandwidth — itu perbaikan
    bug: selama otak bekerja dan selama menunggu suara pertama, tidak ada
    yang bisa dilakukan model dengan audio ruangan. Yang terjadi justru
    sebaliknya — VAD (start sensitivity high) membaca derau sebagai
    "pengguna mulai bicara" dan membatalkan giliran jawaban, sehingga
    JARVIS diam padahal otaknya sudah menjawab (terukur: DIAM 1/5 pada
    VAD high + derau, 0/5 pada low).
    """
    if muted or phone_active:
        return False
    return not (in_brain or awaiting_reply)


def _mouth_fallback_due(pending_since, now: float, got_audio: bool,
                        grace: float = _MOUTH_GRACE_S) -> bool:
    """True bila jawaban otak sudah siap tetapi mulut belum bersuara.

    Jaring pengaman terakhir: kalau server membatalkan giliran jawaban
    (VAD, keepalive, apa pun), jawaban otak tidak boleh hilang — aplikasi
    yang mengucapkannya lewat jalur yang sudah terbukti.
    """
    if pending_since is None or got_audio:
        return False
    return (now - pending_since) >= grace


def _put_drop_oldest(q, item) -> None:
    """Masukkan satu frame audio tanpa pernah melempar.

    Dipanggil dari thread audio lewat ``call_soon_threadsafe``, jadi exception
    di sini tidak bisa ditangkap pemanggil — asyncio mencetaknya sendiri
    sebagai "Exception in callback Queue.put_nowait()" berulang-ulang (terjadi
    23 Okt: ratusan baris QueueFull membanjiri konsol).

    Audio adalah data yang boleh hilang: kalau antrean penuh, frame TERTUA
    dibuang supaya suara terbaru tetap sampai. Memblokir tidak mungkin (thread
    audio realtime), dan membiarkan penuh berarti suara berhenti selamanya.
    """
    try:
        q.put_nowait(item)
        return
    except asyncio.QueueFull:
        pass
    except Exception:                            # noqa: BLE001
        return
    try:
        q.get_nowait()                           # buang yang tertua
    except Exception:                            # noqa: BLE001
        pass
    try:
        q.put_nowait(item)
    except Exception:                            # noqa: BLE001
        pass


# Kode penutupan WebSocket yang berarti "sesi berakhir", bukan "program salah".
# 1008 = policy violation: Gemini memakainya untuk GoAway / batas durasi sesi /
# "The operation was aborted." — semuanya kondisi yang memang dirancang untuk
# disambung ulang, bukan kerusakan.
_WS_CLOSE_CODES = {1000, 1001, 1006, 1008, 1011, 1012, 1013, 1014}


def _is_conn_closed(e: BaseException) -> bool:
    """True bila error berasal dari socket Live yang sudah tertutup.

    24 Okt: `google.genai.errors.APIError` TIDAK masuk daftar nama di bawah,
    padahal ia membawa `status_code` WebSocket. Akibatnya abort 1008 yang sah
    dicetak sebagai traceback 25 baris (lihat main.py `_receive_audio`),
    berulang setiap reconnect — log penuh merah untuk kondisi normal.
    """
    if isinstance(e, TimeoutError):
        return True
    if type(e).__name__ in ("ConnectionClosed", "ConnectionClosedError",
                            "ConnectionClosedOK"):
        return True
    code = getattr(e, "status_code", None)
    if isinstance(code, int) and code in _WS_CLOSE_CODES:
        return True
    # Sebagian pembungkus menaruh kode hanya di teks ("received 1008").
    txt = str(e)[:200]
    return bool(re.search(r"\b(1008|1011|1012|1013)\b", txt))


# ── Backoff untuk abort beruntun ──────────────────────────────────────────
# Sesi yang hidup < 20 detik lalu putus = gagal cepat. Satu kali masih wajar
# (GoAway dari server). Dua kali berturut-turut berarti server masih menolak:
# menyambung ulang tanpa jeda hanya memutar loop (kejadian 24 Okt: 4× dalam
# hitungan detik). Jeda tumbuh 2 → 4 → 8 detik, lalu mentok.
_FAST_ABORT_WINDOW = 20.0
_ABORT_BACKOFF_MAX = 8.0


def _abort_backoff(fast_aborts: int) -> float:
    """Jeda (detik) sebelum menyambung ulang setelah abort beruntun."""
    try:
        n = int(fast_aborts or 0)
    except (TypeError, ValueError):
        return 0.0
    if n <= 1:
        return 0.0                      # kegagalan pertama: coba segera
    return min(2.0 ** (n - 1), _ABORT_BACKOFF_MAX)


def main():
    _single_instance_guard()
    # A native crash (access violation in the audio stack, Qt, or a provider
    # library) otherwise leaves nothing behind but an exit code. With this on,
    # the console prints every thread's Python stack at the moment it dies —
    # the difference between guessing and knowing.
    faulthandler.enable()
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()