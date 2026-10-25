# config/__init__.py
import json, os, platform
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "api_keys.json"
_FLAGS_PATH  = Path(__file__).parent / "voice_flags.json"

# Fase 4 — sakelar adaptasi suara. SATU berkas, SATU boolean per fitur,
# bawaan HIDUP: mematikan salah satu mengembalikan persis perilaku lama, dan
# berkas yang hilang atau rusak jatuh kembali ke bawaan (bukan exception) —
# sakelar rollback tidak boleh bisa membuat aplikasi gagal boot.
VOICE_FLAG_DEFAULTS: dict[str, bool] = {
    "voice.receipt_enabled": True,   # Slice 2 — kode mengucapkan tanda terima
    "recall.auto":            True,   # Slice 4 — ingat relevan otomatis per giliran
    "voice.bargein_note":     True,   # interupsi — ingat ucapan yang terpotong
    "voice.quiet_window":     True,   # Slice 5 — jendela 30 s sebelum hasil bicara
    "voice.turn_classify":    True,   # Slice 1  — klasifikasi pemilik giliran
    "trust.remember":         True,   # Slice 3  — izin per-tool yang diingat
    "voice.silence_gate":     True,   # Kandidat C — gerbang stay-silent
}

def get_flags() -> dict:
    """Sakelar suara Fase 4, digabung di atas bawaannya (config/voice_flags.json).

    Berkas hilang/rusak -> bawaan. Dibaca baru pada setiap pemanggilan:
    mengubah sakelar harus langsung berlaku pada ucapan berikutnya, bukan
    menunggu reconnect — inilah gunanya sakelar."""
    out = dict(VOICE_FLAG_DEFAULTS)
    try:
        with open(_FLAGS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for k, v in raw.items():
                if k in VOICE_FLAG_DEFAULTS:
                    out[k] = bool(v)
    except Exception:
        pass
    return out

def _platform_os() -> str:
    """Auto-detect OS when config file is absent."""
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def get_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", _platform_os()).lower()

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"
