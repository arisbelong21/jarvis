"""Bangun paket ZIP bersih untuk dibagikan/dijual — TANPA menyentuh folder kerja.

Prinsip: WHITELIST. Hanya berkas yang disebut di sini yang masuk ke ZIP.
Berkas baru yang tidak sengaja dibuat (data pribadi, kunci, log) TIDAK akan
ikut hanya karena lupa didaftarkan — itu bedanya dengan blacklist.

Berkas pembantu (setup.bat, start.bat, README) dibaca dari folder
`packaging/`, bukan ditulis di dalam skrip ini: batch yang memuat perintah
sistem lebih mudah diperiksa sebagai berkas tersendiri.

Hasil: dist/JARVIS-SHARE.zip

Jalankan:  python build_package.py
"""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGING = ROOT / "packaging"
OUT_DIR = ROOT / "dist"
OUT_ZIP = OUT_DIR / "JARVIS-SHARE.zip"
STAGE_NAME = "JARVIS-SHARE"

# ── yang BOLEH ikut ────────────────────────────────────────────────────────
ROOT_FILES = [
    "main.py", "ui.py", "ui_face3d.py", "ui_face25d.py", "setup.py",
    "requirements.txt",
    "readme.md", "LICENSE", "run_aris_console.bat",
]

# folder -> pola berkas yang diizinkan
DIR_RULES = {
    "core":       ["*.py", "*.txt"],
    "actions":    ["*.py"],
    "plugins":    ["*.py"],
    "providers":  ["*.py"],
    # dashboard butuh aset statis di dashboard/static/ — kalau tidak ikut,
    # panel web tampil kosong.
    "dashboard":  ["*.py", "*.html", "*.js"],
    "skills":     ["*.md"],
    # wajah 2.5D: gambar dasar untuk ui_face25d.py
    "assets":     ["*.png"],
    # memory: hanya kode. SEMUA .json/.jsonl/.ics/.bak adalah data pengguna.
    "memory":     ["__init__.py", "memory_manager.py", "config_manager.py"],
    # config: hanya berkas produk. api_keys.json & mcp.json DIBUAT ULANG.
    "config":     ["__init__.py", "apps.json", "decision_rules.json",
                   "voice_flags.json", "jarvis.ico"],
}

# ── yang TIDAK PERNAH ikut (jaring kedua, kalau whitelist salah tulis) ─────
NEVER_SUFFIX = (".pyc", ".pyo", ".bak", ".pem", ".key", ".log")
NEVER_PARTS = {"__pycache__", ".venv313", "_refwork", ".workbuddy-ai",
               "logs", "cloud_state", "_archive", ".git", "dist", "certs",
               "packaging",
               # desktop/ = RUANG KERJA pengguna (berkas hasil generate),
               # bukan kode produk. 25 Okt: sempat ikut satu screenshot.
               "desktop"}

# Berkas pembantu yang ditempel ke paket.
EXTRA_FILES = {
    "setup.bat": "setup.bat",
    "start.bat": "start.bat",
    "README-PEMBELI.txt": "README-PEMBELI.txt",
}


def _allowed(rel: Path) -> bool:
    if any(part in NEVER_PARTS for part in rel.parts):
        return False
    if rel.suffix.lower() in NEVER_SUFFIX:
        return False
    return True


def sanitize_api_config(src: Path) -> dict:
    """Salin config dengan SEMUA rahasia & data pribadi dikosongkan."""
    data = json.loads(src.read_text(encoding="utf-8"))

    data["gemini_api_key"] = ""

    for _pid, pcfg in (data.get("providers") or {}).items():
        if isinstance(pcfg, dict):
            pcfg["api_key"] = ""

    # Instalasi baru belum punya provider: pakai mode Gemini penuh.
    data["active_provider"] = ""
    data["voice_brain"] = "live"
    data["user_name"] = ""
    # Perangkat audio pembeli berbeda — biarkan aplikasi memilih otomatis.
    data["input_device"] = ""
    data["output_device"] = ""

    pc = data.get("plugin_config") or {}
    for name in ("calendar", "telegram_remote", "composio"):
        sub = pc.get(name)
        if not isinstance(sub, dict):
            continue
        for field in ("ics_url", "bot_token", "pairing_code", "api_key",
                      "user_id", "share_folder", "inbox_folder"):
            if field in sub:
                sub[field] = ""
        if "allowed_chat_ids" in sub:
            sub["allowed_chat_ids"] = []

    # Plugin yang butuh akun pribadi: jangan menyalakannya di paket bersih.
    enabled = data.get("plugins_enabled") or {}
    for name in ("telegram_remote", "composio_connector", "calendar_assistant"):
        if name in enabled:
            enabled[name] = False
    tg = pc.get("telegram_remote")
    if isinstance(tg, dict) and "start_on_launch" in tg:
        tg["start_on_launch"] = False

    return data


def sanitize_mcp(src: Path) -> dict:
    """Matikan server MCP yang menunjuk path mesin pembuat.

    `rea` memakai folder alat milik mesin ini. Kalau dibiarkan aktif, pembeli
    menunggu 90 detik lalu gagal saat start.
    """
    data = json.loads(src.read_text(encoding="utf-8"))
    for _name, scfg in (data.get("servers") or {}).items():
        if not isinstance(scfg, dict):
            continue
        if scfg.get("env"):
            scfg["env"] = {}
        args = scfg.get("args") or []
        if any("C:\\" in str(a) or "C:/" in str(a) for a in args):
            scfg["enabled"] = False
    rea = (data.get("servers") or {}).get("rea")
    if isinstance(rea, dict):
        rea["enabled"] = False
    return data


def collect() -> list[tuple[Path, Path]]:
    """(sumber, tujuan_relatif) untuk semua berkas yang diizinkan."""
    items: list[tuple[Path, Path]] = []
    for name in ROOT_FILES:
        src = ROOT / name
        if src.is_file():
            items.append((src, Path(name)))
    for folder, patterns in DIR_RULES.items():
        base = ROOT / folder
        if not base.is_dir():
            continue
        for pat in patterns:
            # rglob: aset di subfolder (dashboard/static/) ikut, bukan hanya
            # yang di akar folder.
            for src in sorted(base.rglob(pat)):
                if not src.is_file():
                    continue
                rel = src.relative_to(ROOT)
                if _allowed(rel):
                    items.append((src, rel))
    return items


def build() -> Path:
    missing = [n for n in EXTRA_FILES if not (PACKAGING / n).is_file()]
    if missing:
        raise SystemExit(f"Berkas pembantu belum ada di packaging/: {missing}")

    OUT_DIR.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="jarvis-share-")) / STAGE_NAME
    stage.mkdir(parents=True)

    for src, rel in collect():
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # Config yang dibuat ulang (bukan disalin)
    (stage / "config").mkdir(exist_ok=True)
    cfg = sanitize_api_config(ROOT / "config" / "api_keys.json")
    (stage / "config" / "api_keys.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    mcp_src = ROOT / "config" / "mcp.json"
    if mcp_src.is_file():
        (stage / "config" / "mcp.json").write_text(
            json.dumps(sanitize_mcp(mcp_src), indent=2, ensure_ascii=False),
            encoding="utf-8")

    for name, src_name in EXTRA_FILES.items():
        shutil.copy2(PACKAGING / src_name, stage / name)

    # Batch file WAJIB CRLF. Berkas berakhiran LF membuat `cmd` berhenti di
    # tengah saat menemui `goto` — kejadian 25 Okt waktu menguji setup.bat:
    # skrip berhenti setelah "[1/4] Mencari Python" tanpa pesan apa pun.
    for bat in stage.rglob("*.bat"):
        text = bat.read_text(encoding="utf-8", errors="ignore")
        bat.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n")
                        .encode("utf-8"))

    if OUT_ZIP.exists():
        OUT_ZIP.unlink()
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                # Semua entri di bawah satu folder induk: pembeli mengekstrak
                # satu folder, bukan menumpahkan 10 folder ke tempat acak.
                arc = f"{STAGE_NAME}/{p.relative_to(stage).as_posix()}"
                zf.write(p, arc)

    shutil.rmtree(stage.parent, ignore_errors=True)
    return OUT_ZIP


def main() -> None:
    t0 = datetime.now()
    zpath = build()
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        total = sum(i.file_size for i in zf.infolist())
    print(f"[PAKET] {zpath}")
    print(f"[PAKET] {len(names)} berkas · isi asli {total/1024:.0f} KB · "
          f"zip {zpath.stat().st_size/1024:.0f} KB · "
          f"{(datetime.now()-t0).total_seconds():.1f}s")
    tops: dict[str, int] = {}
    for n in names:
        key = n.split("/")[0] if "/" in n else "(root)"
        tops[key] = tops.get(key, 0) + 1
    print("[PAKET] isi per folder:",
          ", ".join(f"{k}={v}" for k, v in sorted(tops.items())))


if __name__ == "__main__":
    main()
