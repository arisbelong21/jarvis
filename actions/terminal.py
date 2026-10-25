"""
actions/terminal.py — `run_command`: terminal terstruktur (GrokBot 22 Okt).

Sebelum ini JARVIS hanya bisa MENJALANKAN BERKAS (code_helper action=run) —
tidak ada cara terstruktur untuk `git status`, `pytest`, `dir`, `npm test`.
Tool ini mengembalikan exit code + stdout/stderr yang dipotong, sehingga
siklus baca → ubah → jalankan → verifikasi bisa dikerjakan otak kustom
dengan bukti nyata.

Keamanan
    • Izin diputuskan di pintu pusat (core/decision_loop.gate):
      perintah hanya-baca → jalan; perintah yang mengubah sistem
      (hapus, format, registry, shutdown, instal, git push/reset --hard,
      apa pun yang tidak dikenali) → WAJIB konfirmasi Aris lewat mekanisme
      konfirmasi yang sudah ada. Tidak bisa di-trust permanen.
    • Tidak ada shell=True. Perintah diserahkan sebagai SATU argumen ke
      powershell -Command / cmd /c (Windows) atau bash -lc (lainnya) —
      tidak digabung dengan teks lain.
    • cwd harus folder yang ada; batas waktu maks 300 s; keluaran dipotong.
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

_MAX_OUT = 4000
_MAX_TIMEOUT = 300
_WIN = platform.system() == "Windows"
_NO_WINDOW = {"creationflags": 0x08000000} if _WIN else {}

# Audit 26 Okt — verifikasi berbasis kontrak (core/contract.py). Import dijaga
# agar tool tetap jalan bila core tak tersedia.
try:
    from core import contract as _contract
except Exception:                                # noqa: BLE001  # pragma: no cover
    _contract = None


def _rc(text: str, ok, **ev) -> str:
    if _contract is None:
        return text
    fn = {True: _contract.ok, False: _contract.fail}.get(ok, _contract.unknown)
    return fn(text, tool="run_command", **ev)



def _default_cwd() -> Path:
    try:
        from core import workspace
        return Path(workspace.target_dir())
    except Exception:                                    # noqa: BLE001
        return Path.home()


def build_argv(command: str, shell: str = "") -> list:
    """argv untuk satu perintah — perintah selalu SATU elemen."""
    sh = (shell or ("powershell" if _WIN else "bash")).lower()
    if sh in ("powershell", "pwsh", "ps"):
        exe = "pwsh" if sh == "pwsh" else "powershell"
        return [exe, "-NoProfile", "-NonInteractive", "-Command", command]
    if sh == "cmd":
        return ["cmd.exe", "/d", "/c", command]
    return ["bash", "-lc", command]


def _clip(text: str) -> str:
    text = text or ""
    if len(text) <= _MAX_OUT:
        return text
    half = _MAX_OUT // 2
    return text[:half] + f"\n…[{len(text) - _MAX_OUT} karakter dipotong]…\n" + text[-half:]


def run_command(parameters=None, player=None, **_kw) -> str:
    p = parameters or {}
    command = str(p.get("command") or "").strip()
    if not command:
        return _rc("No command given.", False, reason="no_command")
    try:
        timeout = max(1, min(_MAX_TIMEOUT, int(p.get("timeout") or 60)))
    except (TypeError, ValueError):
        timeout = 60
    cwd = Path(os.path.expandvars(os.path.expanduser(str(p.get("cwd") or "")))) \
        if p.get("cwd") else _default_cwd()
    if not cwd.is_dir():
        return _rc(f"ERROR: folder kerja tidak ada: {cwd}", False, reason="bad_cwd")
    if player:
        try:
            player.write_log(f"[Terminal] {command[:120]}")
        except Exception:                                # noqa: BLE001
            pass
    argv = build_argv(command, str(p.get("shell") or ""))
    try:
        r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout, **_NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        return _rc(f"exit_code: timeout after {timeout}s\ncwd: {cwd}\n"
                   f"stdout:\n{_clip(out)}", False, reason="timeout", exit_code=None)
    except FileNotFoundError as e:
        return _rc(f"ERROR: shell tidak ditemukan ({argv[0]}): {e}", False,
                   reason="shell_not_found")
    return _rc(f"exit_code: {r.returncode}\ncwd: {cwd}\n"
               f"stdout:\n{_clip(r.stdout)}\nstderr:\n{_clip(r.stderr)}",
               r.returncode == 0, exit_code=r.returncode)


TOOL = {
    "name": "run_command",
    "description": (
        "Run ONE terminal command on this computer and get its exit code, "
        "stdout and stderr (PowerShell by default on Windows). Use it to check "
        "and verify work: git status/diff, run tests, list folders, read "
        "versions. Read-only commands run at once; anything that changes the "
        "system (delete, install, registry, shutdown, git push, writing files) "
        "asks the user first. Report success only when exit_code is 0."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "command": {"type": "STRING",
                        "description": "The exact command line to run"},
            "cwd": {"type": "STRING",
                    "description": "Working folder (default: the current work folder)"},
            "shell": {"type": "STRING",
                      "description": "powershell | cmd | bash (default: powershell on Windows)"},
            "timeout": {"type": "INTEGER",
                        "description": "Seconds before it is stopped (default 60, max 300)"},
        },
        "required": ["command"],
    },
    "handler": run_command,
}
