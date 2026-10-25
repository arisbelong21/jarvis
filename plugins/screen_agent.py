"""
plugins/screen_agent.py — lihat layar, putuskan, klik, ulangi.

Ini fitur yang membuat Jarvis bisa mengoperasikan aplikasi APA PUN yang bisa
diklik manusia: Blender, Photoshop, Excel, browser, aplikasi internal — tanpa
API, tanpa integrasi khusus.

CARA KERJA (satu langkah = satu tangkapan layar)
    screenshot → model visi memutuskan SATU aksi → pyautogui mengeksekusi
    → screenshot lagi → … sampai "done", "fail", atau batas langkah habis.

PENGAMAN (sengaja dipasang, bukan opsional)
    • `max_steps` dibatasi 12 — tidak ada loop tak berujung.
    • `pyautogui.FAILSAFE` aktif: geser mouse ke pojok kiri-atas = berhenti.
    • Setiap aksi dicatat ke konsol dan panel konten, jadi apa yang dilakukan
      Jarvis bisa diperiksa setelahnya.
    • Bisa dimatikan total lewat config: `"screen_agent_enabled": false`.
    • Model diminta menyertakan alasan singkat untuk setiap aksi — itu yang
      membuat kesalahan mudah dilacak.

BIAYA: satu panggilan model visi per langkah. Tugas 6 langkah = 6 panggilan.
Untuk tugas yang bisa dilakukan lewat tool biasa (buka app, ketik teks),
tool itu lebih murah dan lebih tepat — agent ini untuk yang memang butuh
mata dan tangan.
"""

import base64
import json
import re
import time

PLUGIN = {
    "name": "screen_agent",
    "description": (
        "Mengoperasikan aplikasi di layar seperti manusia: melihat tangkapan "
        "layar, lalu mengklik, mengetik, dan menggulir sampai tugas selesai. "
        "Gunakan untuk tugas yang TIDAK punya tool khusus — mis. 'buka "
        "Blender lalu tambahkan silinder dan geser 3 meter di sumbu X', "
        "'di Photoshop perbesar gambar 200%', 'klik tombol Simpan di aplikasi "
        "itu'. JANGAN pakai untuk hal yang sudah ada tool-nya (buka aplikasi, "
        "kirim email, cari di web) — itu lebih cepat dan lebih aman. "
        "Kirim tujuan pengguna apa adanya di 'goal'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "goal": {
                "type": "STRING",
                "description": ("Tujuan yang harus dicapai di layar, verbatim "
                                "dari pengguna."),
            },
            "max_steps": {
                "type": "INTEGER",
                "description": ("Batas langkah (default 6, maksimum 12). "
                                "Satu langkah = satu aksi."),
            },
            "dry_run": {
                "type": "BOOLEAN",
                "description": ("true = HANYA melihat layar dan melaporkan "
                                "rencana aksi, TANPA mengklik atau mengetik. "
                                "Pakai saat pengguna ingin memeriksa dulu "
                                "('lihat dulu, jangan klik apa pun')."),
            },
        },
        "required": ["goal"],
    },
}

_MODEL = "gemini-flash-latest"
_HARD_CAP = 12


# ── tangkap layar ────────────────────────────────────────────────────────────

def _grab():
    """(png_bytes, width, height) untuk monitor utama."""
    import mss
    from PIL import Image
    import io

    with mss.mss() as sct:
        mon = sct.monitors[1]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), img.width, img.height


# ── keputusan model ──────────────────────────────────────────────────────────

_PROMPT = """Kamu adalah agen GUI. Tujuan pengguna: {goal}

Ini tangkapan layar ke-{step} (ukuran {w}x{h} piksel). Riwayat aksi:
{history}

Putuskan SATU aksi berikutnya. Balas HANYA JSON minified, tanpa markdown:
{{"action":"click|type|hotkey|scroll|wait|done|fail","x":0,"y":0,
 "text":"","keys":[],"amount":0,"reason":"alasan singkat"}}

Aturan:
- "click" butuh x,y dalam piksel gambar (0..{w}, 0..{h}).
- "type" butuh text. "hotkey" butuh keys (mis. ["ctrl","s"]).
- "scroll" butuh amount (positif naik, negatif turun).
- "done" HANYA bila tujuan sudah benar-benar tercapai.
- "fail" bila layar tidak menunjukkan apa yang diharapkan.
- Jangan mengulang aksi yang sudah gagal; pilih pendekatan lain.
- Jangan menebak: kalau tombolnya tidak terlihat, scroll atau tunggu.
"""


def _decide(goal: str, png: bytes, w: int, h: int, step: int,
            history: list[str]) -> dict:
    from google import genai
    from google.genai import types
    from memory.config_manager import get_gemini_key

    key = get_gemini_key()
    if not key:
        raise RuntimeError("kunci Gemini tidak ada — agent layar butuh model visi")

    client = genai.Client(api_key=key)
    prompt = _PROMPT.format(goal=goal, step=step, w=w, h=h,
                            history="\n".join(history[-6:]) or "(belum ada)")
    resp = client.models.generate_content(
        model=_MODEL,
        contents=[prompt, types.Part.from_bytes(data=png, mime_type="image/png")],
    )
    return _parse_action(resp.text or "")


def _parse_action(raw: str) -> dict:
    """JSON dari model, tahan terhadap pagar markdown dan teks pembuka."""
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict) or "action" not in data:
        raise ValueError(f"aksi tidak valid: {text[:120]}")
    return data


# ── eksekusi ─────────────────────────────────────────────────────────────────

def _apply(act: dict) -> str:
    """Jalankan satu aksi. Mengembalikan deskripsi singkat untuk riwayat."""
    import pyautogui

    pyautogui.FAILSAFE = True          # pojok kiri-atas = berhenti
    pyautogui.PAUSE = 0.15

    kind = str(act.get("action") or "").lower()
    if kind == "click":
        x, y = int(act.get("x") or 0), int(act.get("y") or 0)
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.click()
        return f"klik ({x},{y})"
    if kind == "type":
        text = str(act.get("text") or "")
        pyautogui.write(text, interval=0.02)
        return f"ketik {text[:40]!r}"
    if kind == "hotkey":
        keys = [str(k) for k in (act.get("keys") or [])]
        if not keys:
            return "hotkey kosong (dilewati)"
        pyautogui.hotkey(*keys)
        return "hotkey " + "+".join(keys)
    if kind == "scroll":
        amount = int(act.get("amount") or -3)
        pyautogui.scroll(amount)
        return f"scroll {amount}"
    if kind == "wait":
        time.sleep(float(act.get("seconds") or 1.0))
        return "tunggu"
    return f"aksi tak dikenal: {kind}"


def _disabled() -> bool:
    try:
        from memory.config_manager import load_api_keys
        cfg = load_api_keys()
        return cfg.get("screen_agent_enabled") is False
    except Exception:
        return False


# ── entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    goal = str((parameters or {}).get("goal") or "").strip()
    if not goal:
        return "Sebutkan tugas yang harus dilakukan di layar."
    if _disabled():
        return ("Agent layar dimatikan di pengaturan "
                "('screen_agent_enabled': false).")

    try:
        steps = int((parameters or {}).get("max_steps") or 6)
    except Exception:
        steps = 6
    steps = max(1, min(_HARD_CAP, steps))
    dry = bool((parameters or {}).get("dry_run"))

    history: list[str] = []
    for i in range(1, steps + 1):
        try:
            png, w, h = _grab()
        except Exception as e:                       # noqa: BLE001
            return f"Tidak bisa menangkap layar: {e}"

        try:
            act = _decide(goal, png, w, h, i, history)
        except Exception as e:                       # noqa: BLE001
            return (f"Langkah {i}: model visi gagal memutuskan ({e}). "
                    f"Sudah dilakukan: {'; '.join(history) or 'belum ada'}.")

        kind = str(act.get("action") or "").lower()
        reason = str(act.get("reason") or "").strip()
        if kind == "done":
            if dry:
                done_msg = ("Rencana selesai (dry-run — tidak ada klik atau "
                            "ketikan). " + " | ".join(history)
                            if history else
                            "Rencana selesai (dry-run) — model menilai tujuan "
                            "sudah tercapai tanpa aksi.")
            else:
                done_msg = (f"Selesai dalam {i - 1} langkah. "
                            f"Terakhir: {history[-1] if history else 'tidak ada aksi'}.")
            print(f"[ScreenAgent] ✅ {done_msg}")
            return done_msg
        if kind == "fail":
            return (f"Gagal di langkah {i}: {reason or 'layar tidak sesuai'}. "
                    f"Sudah dilakukan: {'; '.join(history) or 'belum ada'}.")

        if dry:
            # Rencana saja: tidak ada klik, tidak ada ketikan. Ini yang
            # membuat percobaan pertama aman — pengguna melihat penilaian
            # model sebelum memberinya mouse.
            plan = (f"rencana: {kind} "
                    + (f"di ({act.get('x')},{act.get('y')}) " if kind == "click" else "")
                    + (f"{act.get('text')!r} " if act.get("text") else "")
                    + (f"{'+'.join(act.get('keys') or [])} " if act.get("keys") else "")
                    + (f"— {reason}" if reason else ""))
            history.append(f"{i}. {plan}")
            print(f"[ScreenAgent] (dry-run) {plan}")
            if player:
                try:
                    player.show_content("🖥️ SCREEN AGENT — RENCANA",
                                        "\n".join(history))
                except Exception:
                    pass
            time.sleep(0.4)
            continue

        try:
            what = _apply(act)
        except Exception as e:                       # noqa: BLE001
            return (f"Aksi '{kind}' gagal dijalankan: {e}. "
                    f"Sudah dilakukan: {'; '.join(history) or 'belum ada'}.")

        line = f"{i}. {what}" + (f" — {reason}" if reason else "")
        history.append(line)
        print(f"[ScreenAgent] {line}")
        if player:
            try:
                player.show_content("🖥️ SCREEN AGENT",
                                    "\n".join(history) + f"\n\nLangkah {i}/{steps}")
            except Exception:
                pass
        time.sleep(0.6)

    if dry:
        return ("Rencana selesai (dry-run — tidak ada klik atau ketikan). "
                + " | ".join(history))
    return ("Batas langkah habis sebelum tujuan tercapai. "
            f"Sudah dilakukan: {'; '.join(history)}.")
