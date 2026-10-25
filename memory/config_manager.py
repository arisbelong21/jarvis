import json
import os
import sys
import time
from threading import Lock
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"
# Audit-3 25 Okt: UI (thread Qt) dan tugas asyncio bisa menulis
# config bersamaan. Tanpa kunci, os.replace bertabrakan dan
# melempar PermissionError setelah 3 percobaan.
_cfg_lock = Lock()

def _atomic_write_json(path: Path, data: dict) -> None:
    """Tulis config secara ATOMIK + simpan cadangan.

    Audit-2 25 Okt: `api_keys.json` ditulis langsung di 6 tempat. Proses yang
    mati di tengah penulisan meninggalkan JSON terpotong — dan karena
    `load_api_keys` mengembalikan {} saat gagal parse, penyimpanan berikutnya
    menghapus SEMUA kunci & pengaturan pengguna.
    """
    with _cfg_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        try:
            if path.exists():
                json.loads(path.read_text(encoding="utf-8"))   # hanya bila sah
                path.with_suffix(path.suffix + ".bak").write_bytes(
                    path.read_bytes())
        except Exception:                   # noqa: BLE001
            pass
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.08)


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def save_config(data: dict) -> None:
    """Simpan seluruh config lewat jalur ATOMIK (dipakai UI juga).

    Audit-3 25 Okt: `ui.py` dulu menulis `api_keys.json` langsung dengan
    `write_text`, melewati helper atomik — jadi perbaikan A3 belum menutup
    jalur UI.
    """
    _atomic_write_json(CONFIG_FILE, dict(data or {}))


def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()

    data = load_api_keys()

    data["gemini_api_key"] = gemini_api_key.strip()

    _atomic_write_json(CONFIG_FILE, data)

def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        # Audit-2 25 Okt: jangan hanya mencetak lalu mengembalikan {}. Tanpa
        # salinan, penyimpanan berikutnya akan menimpa config rusak itu dengan
        # {} + satu kunci baru — semua pengaturan pengguna hilang.
        print(f"❌ Failed to load api_keys.json: {e}")
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            corrupt = CONFIG_FILE.with_name(f"api_keys.corrupt-{stamp}.json")
            CONFIG_FILE.rename(corrupt)
            print(f"[Config] berkas rusak disimpan sebagai {corrupt.name}")
        except Exception:                       # noqa: BLE001
            pass
        bak = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".bak")
        if bak.exists():
            try:
                data = json.loads(bak.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    print("[Config] ✅ dipulihkan dari cadangan .bak")
                    # Audit 25 Okt (K-1): pemulihan WAJIB ditulis balik ke
                    # disk. Tanpa ini file utama lenyap dan setter berikutnya
                    # mulai dari {} — semua provider/kunci/pengaturan hilang
                    # persis seperti bug yang coba ditutup Audit-2. Pola sama
                    # dengan memory_manager.load_memory().
                    _atomic_write_json(CONFIG_FILE, data)
                    return data
            except Exception:                   # noqa: BLE001
                pass
        return {}

def get_gemini_key() -> str | None:
    return load_api_keys().get("gemini_api_key")


def set_voice_brain(mode: str) -> str:
    """Choose the engine: 'live' or 'custom'.

    live   — Gemini thinks and speaks (one model, fastest to answer).
    custom — hybrid: Gemini is ears and mouth, the configured provider is the
             brain. Read per connect in main.py, so it takes effect on the next
             connection rather than mid-sentence.

    Only this one key is touched; everything else in the file is preserved and
    the write is atomic, because this file also carries the API keys.
    """
    mode = (mode or "").strip().lower()
    if mode not in ("live", "custom"):
        raise ValueError("mode harus 'live' atau 'custom'")
    # Audit 25 Okt (M-2): dulu menulis tmp->replace sendiri, melewati
    # _atomic_write_json — tanpa _cfg_lock, tanpa fsync, tanpa .bak, dan bila
    # file sedang korup ia MENIMPA file itu (data tak bisa dipulihkan manual).
    # _patch_config melakukan read-modify-write lewat jalur atomik yang sama
    # dengan semua setter lain, dan memulihkan dari .bak bila file korup.
    _patch_config(voice_brain=mode)
    return mode

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'JARVIS' if not set."""
    return load_api_keys().get("assistant_name", "JARVIS") or "JARVIS"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data = load_api_keys()
    data["assistant_name"] = assistant_name.strip() or "JARVIS"
    data["user_name"] = user_name.strip()
    _atomic_write_json(CONFIG_FILE, data)


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE    = "Charon"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data = load_api_keys()
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    _atomic_write_json(CONFIG_FILE, data)


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Jarvis')."""
    return load_api_keys().get("wake_word_enabled", False)


# Score the wake model must reach before JARVIS wakes — 0 to 1, lower is more
# sensitive. Default 0.35 rather than the library's 0.5: the two errors are
# not equal. A missed wake is what the user feels ("I called and called and
# nothing happened"), while a false wake is nearly free — wake() makes no
# sound and changes no session, the assistant simply stops sleeping. A quiet
# mic, a soft voice or an accent the English-trained model scores lower should
# not have to fight a strict bar. If false wakes DO bother you, raise it; if
# it still ignores you, lower it — the console prints your best recent score
# every 10 s while asleep ("peak 0.28 (needs 0.35)") so the number is known.
def get_wake_threshold() -> float:
    try:
        v = float(load_api_keys().get("wake_threshold", 0.35))
    except (TypeError, ValueError):
        return 0.35
    return max(0.05, min(0.95, v))


def save_wake_threshold(value: float) -> None:
    try:
        v = max(0.05, min(0.95, float(value)))
    except (TypeError, ValueError):
        return
    _patch_config(wake_threshold=v)


def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data = load_api_keys()
    data["wake_word_enabled"] = bool(enabled)
    _atomic_write_json(CONFIG_FILE, data)


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


# Audit 25 Okt: daftar ini adalah SATU-SATUNYA gerbang validasi gaya HUD.
# Saat gaya "face25d" ditambahkan ke ui.py, daftar ini tidak ikut diperbarui —
# akibatnya save_hud_style("face25d") DIAM-DIAM menulis "face", get_hud_style()
# tidak pernah mengembalikan "face25d", dan siklus tombol berhenti di dua gaya
# (face <-> face25d) sehingga core & orbit seolah hilang. Setiap gaya baru di
# ui._toggle_hud_style WAJIB ditambahkan di sini.
HUD_STYLES = ("face", "face25d", "core", "orbit")


def get_hud_style() -> str:
    """Which centrepiece the HUD draws: the animated head, the 2.5D mask,
    the reactor core, or the mockup's orbit scene (wireframe head in rings).

    Taste, not capability — all four render in the same software painter and
    cost about the same. Defaults to the head because that is what MARK LIV
    shipped with; anyone who preferred the older look can switch back in ⚙ and
    the choice survives a restart.
    """
    v = str(load_api_keys().get("hud_style", "face")).strip().lower()
    return v if v in HUD_STYLES else "face"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip().lower()
    if s not in HUD_STYLES:
        # Jangan diam: bug 25 Okt terjadi justru karena nilai yang tidak
        # dikenal diganti "face" tanpa sepatah kata. Sekarang terlihat di log.
        print(f"[HUD] gaya tak dikenal {s!r} — jatuh ke 'face'. "
              f"Pilihan sah: {', '.join(HUD_STYLES)}")
        s = "face"
    _save_flag("hud_style", s)


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.

def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled":    bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms":  _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity":   str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


def save_turn_tuning(values: dict) -> None:
    ensure_config_dir()
    data = load_api_keys()
    cur = data.get("turn_tuning")
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    data["turn_tuning"] = cur
    _atomic_write_json(CONFIG_FILE, data)


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


def get_animations_enabled() -> bool:
    """Whether the HUD draws its looping animations (orbit rings, reactor
    arcs, scanline, float). On by default; switching it off freezes every
    looping animation on its current frame — the UI stays fully usable, the
    mouth still tracks speech, and idle CPU use drops to near nothing."""
    return bool(load_api_keys().get("hud_animations", True))


def save_animations_enabled(enabled: bool) -> None:
    _save_flag("hud_animations", bool(enabled))


def get_privacy_shield() -> dict:
    """The real state the Privacy Shield chip reports. Three checks, each
    answering a question a privacy-minded user actually asks:
    - keys_local: API keys live only in this machine's config folder
      (they always do — the file is the only store — so this is true unless
      the file is unreadable, which would mean something is very wrong).
    - voice_local: the wake word runs on-device (openwakeword), and the mic
      gate is under the user's thumb — true by construction, shown so the
      chip is a summary, not a promise.
    - screen_guard: screen captures only leave the machine when the user
      explicitly asks JARVIS to look at something. The flag records the
      user's standing choice for clipboard/screen scanning.
    """
    cfg = load_api_keys()
    return {
        "keys_local": CONFIG_FILE.is_file(),
        "voice_local": True,
        "screen_guard": bool(cfg.get("privacy_screen_guard", True)),
        "enabled": bool(cfg.get("privacy_shield", True)),
    }


def save_privacy_shield(enabled: bool) -> None:
    _save_flag("privacy_shield", bool(enabled))


def save_privacy_screen_guard(enabled: bool) -> None:
    _save_flag("privacy_screen_guard", bool(enabled))


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    ensure_config_dir()
    data = load_api_keys()
    data[key] = bool(value) if isinstance(value, bool) else value
    _atomic_write_json(CONFIG_FILE, data)


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data = load_api_keys()
    data["morning_brief_enabled"] = enabled
    _atomic_write_json(CONFIG_FILE, data)


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine.

    Audit 25 Okt (M-2): membaca lewat load_api_keys() — bukan parse langsung —
    supaya jalur pemulihan K-1 (karantina file korup + pulih dari .bak + tulis
    balik) juga berlaku saat sebuah setter berjalan. Tanpa ini, setter yang
    memanggil _patch_config saat file korup akan memulai dari {} dan membuang
    cadangan .bak yang sebenarnya bisa dipulihkan.
    """
    ensure_config_dir()
    data = load_api_keys()
    data.update(fields)
    _atomic_write_json(CONFIG_FILE, data)


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data = load_api_keys()
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    cur.update(values)
    pc[namespace] = cur
    data["plugin_config"] = pc
    _atomic_write_json(CONFIG_FILE, data)


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data = load_api_keys()
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    _atomic_write_json(CONFIG_FILE, data)


# ── External model providers (OpenAI-compatible) ─────────────────────────────
# A provider is an external model joined to core/gemini.py's ladder — a coder
# model leading the SMART tier, say, with the Gemini rungs behind it as a
# fallback. Stored per id under api_keys.json["providers"][<pid>]:
#
#   {
#     "base_url": "https://api.example.com/v1",
#     "api_key":  "sk-...",
#     "model":    "some-coder-model",
#     "vision":   false,            # can this model read images?
#     "tiers":    ["smart"]         # which ladder tiers it serves
#   }
#
# provider_first decides whether a configured provider leads the ladder or
# only backs it up. See core/gemini.py (_provider_rungs) and the providers/
# package. The API key is stored exactly like the Gemini key — plaintext,
# on this machine, treated like a password file.

def get_provider_config(pid: str) -> dict:
    """All stored values for one provider id ({} if unset)."""
    cfg = load_api_keys().get("providers")
    val = cfg.get(pid) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def save_provider_config(pid: str, values: dict) -> None:
    """Merge `values` into one provider's stored config (read-modify-write,
    like every other setter here). Only the provided keys are touched."""
    ensure_config_dir()
    data = load_api_keys()
    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    cur = providers.get(pid)
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    providers[pid] = cur
    data["providers"] = providers
    _atomic_write_json(CONFIG_FILE, data)


def get_provider_first() -> bool:
    """Whether a configured provider leads the ladder (True) or only backs
    the Gemini names up. Default: lead — that is the point of configuring
    one."""
    return bool(load_api_keys().get("provider_first", True))


def save_provider_first(value: bool) -> None:
    _patch_config(provider_first=bool(value))


def list_provider_ids() -> list[str]:
    """All configured provider ids, in storage order."""
    cfg = load_api_keys().get("providers")
    if isinstance(cfg, dict):
        return [str(pid) for pid in cfg.keys()]
    return []


def get_active_provider() -> str:
    """Which provider leads the heavy-work ladder. "" = the first one."""
    return str(load_api_keys().get("active_provider", "") or "").strip()


def save_active_provider(pid: str) -> None:
    _patch_config(active_provider=(pid or "").strip())


def delete_provider(pid: str) -> None:
    """Remove one provider from the ladder. Also clears active_provider if it
    pointed at it, so the switch never dangles at a provider that is gone."""
    ensure_config_dir()
    data = load_api_keys()
    providers = data.get("providers")
    if isinstance(providers, dict):
        providers.pop(pid, None)
        data["providers"] = providers
    if str(data.get("active_provider", "")).strip() == pid:
        data["active_provider"] = ""
    _atomic_write_json(CONFIG_FILE, data)