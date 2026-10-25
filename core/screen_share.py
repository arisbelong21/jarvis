"""
core/screen_share.py — lihat layar yang dibagikan, lalu BIMBING (bukan klik).

Bedanya dengan plugins/screen_agent.py:
    screen_agent  = tangan  → mengklik dan mengetik sendiri.
    screen_share  = mata    → melihat layar, menjelaskan, dan menunjuk
                              ("bukan itu — tombol Simpan ada di kiri atas").

Kenapa dipisah: membimbing itu aman. Kalau Jarvis salah paham, yang terjadi
hanya nasihat keliru — bukan klik yang tidak diinginkan. Mode ini dipakai
untuk "share screen" di UI: pengguna mengerjakan sendiri, Jarvis mengawasi.

Keluaran setiap analisis:
    {"hint": "satu kalimat petunjuk", "point": {"x":…, "y":…} | None,
     "ok": bool}
`point` dipakai UI untuk menggambar penanda di atas pratinjau layar.
"""

from __future__ import annotations

import io
import json
import re

_MODEL = "gemini-flash-latest"

_PROMPT = """Kamu sedang MEMBIMBING pengguna mengoperasikan layarnya sendiri.
Tujuan pengguna: {goal}

Ini tangkapan layar saat ini ({w}x{h}). Petunjuk sebelumnya:
{last}

Tugasmu: beri SATU petunjuk singkat dan konkret tentang langkah berikutnya.
Balas HANYA JSON minified:
{{"hint":"satu kalimat, bahasa yang sama dengan tujuan","x":0,"y":0,"ok":false}}

Aturan:
- "hint" maksimal 18 kata, langsung bisa dikerjakan.
- Isi x,y (piksel gambar) bila menunjuk elemen tertentu; 0,0 bila tidak.
- "ok": true HANYA bila tujuan sudah tercapai di layar ini.
- Kalau pengguna SALAH klik/berada di tempat yang salah, katakan dengan
  tenang apa yang salah dan ke mana harus mengklik.
- Jangan menyuruh hal yang tidak terlihat di layar.
"""


def capture():
    """(png_bytes, width, height) monitor utama — untuk model visi."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), img.width, img.height


def capture_fast(max_width: int = 900, quality: int = 60):
    """(jpeg_bytes, w, h) untuk PRATINJAU — jalur cepat.

    Diukur di mesin ini: PIL resize+encode 22,6 ms/frame, OpenCV 3,4 ms —
    6,6× lebih cepat, dan itu selisih antara pratinjau 2 fps dan 12 fps.
    Model visi tetap memakai `capture()` (PNG penuh) karena butuh detail
    untuk menentukan koordinat; pratinjau hanya butuh terlihat mulus.
    """
    import mss
    import numpy as np

    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        frame = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(
            shot.height, shot.width, 4)
        frame = frame[:, :, :3]          # BGRA → BGR, tanpa copy tambahan

    try:
        import cv2
        h, w = frame.shape[:2]
        if w > max_width:
            nh = max(1, int(h * max_width / w))
            frame = cv2.resize(frame, (max_width, nh),
                               interpolation=cv2.INTER_AREA)
        ok, enc = cv2.imencode(".jpg", frame,
                               [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            return enc.tobytes(), frame.shape[1], frame.shape[0]
    except Exception:
        pass

    # Fallback tanpa OpenCV: PIL, lebih lambat tapi tetap jalan.
    from PIL import Image
    img = Image.fromarray(frame[:, :, ::-1])
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, max(1, int(img.height * ratio))))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue(), img.width, img.height


def preview_jpeg(png: bytes, width: int = 900) -> bytes:
    """Versi kecil untuk pratinjau di HUD — hemat memori & cepat digambar."""
    from PIL import Image

    img = Image.open(io.BytesIO(png))
    if img.width > width:
        ratio = width / img.width
        img = img.resize((width, max(1, int(img.height * ratio))))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=72)
    return buf.getvalue()


def _parse(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict) or not data.get("hint"):
        raise ValueError(f"petunjuk tidak valid: {text[:120]}")
    point = None
    try:
        x, y = int(data.get("x") or 0), int(data.get("y") or 0)
        if x > 0 and y > 0:
            point = {"x": x, "y": y}
    except Exception:
        point = None
    return {"hint": str(data["hint"]).strip()[:220],
            "point": point,
            "ok": bool(data.get("ok"))}


def advise(goal: str, png: bytes, w: int, h: int,
           last: str = "") -> dict:
    """Satu analisis: apa langkah berikutnya di layar ini?"""
    from google import genai
    from google.genai import types
    from memory.config_manager import get_gemini_key

    key = get_gemini_key()
    if not key:
        raise RuntimeError("kunci Gemini tidak ada — mode bimbing butuh model visi")

    client = genai.Client(api_key=key)
    prompt = _PROMPT.format(goal=goal or "bantu pengguna menyelesaikan tugasnya",
                            w=w, h=h, last=last or "(belum ada)")
    resp = client.models.generate_content(
        model=_MODEL,
        contents=[prompt, types.Part.from_bytes(data=png,
                                                mime_type="image/png")])
    return _parse(resp.text or "")
