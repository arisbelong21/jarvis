"""
core/agent/verifier.py — "apakah langkah ini benar-benar berhasil?"

Ini bagian yang membedakan agent dari sekadar pemanggil tool. Tanpa
verifikasi, kegagalan baru terlihat di akhir; dengan verifikasi, langkah yang
salah bisa diulang selagi masih murah.

Penilaian memakai tiga sumber, berurutan dari yang paling murah:

    1. hasil kosong / error yang jelas dari tool  → gagal, tanpa model
    2. kriteria `success` di rencana              → dinilai model
    3. kalau model tidak bisa menilai             → anggap berhasil bila ada
       hasil, dan tandai "unverified" supaya jujur di laporan akhir

Verifikasi TIDAK boleh memanggil tool. Ia hanya membaca hasil yang sudah ada.
"""
from __future__ import annotations

import json
import re

FAIL_MARKERS = ("error", "gagal", "failed", "traceback", "not found",
                "tidak ditemukan", "tidak bisa", "denied", "ditolak",
                "exception", "no such file")


def _extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def looks_failed(result: str) -> bool:
    """Deteksi cepat kegagalan yang tidak perlu ditanyakan ke model."""
    low = (result or "").strip().lower()
    if not low:
        return True
    if low.startswith(("error", "failed", "gagal", "traceback")):
        return True
    return any(m in low[:160] for m in FAIL_MARKERS)


def verify(step: dict, result: str, llm=None) -> dict:
    """{"ok": bool, "reason": str, "fix": str, "unverified": bool}"""
    result = (result or "").strip()
    if looks_failed(result):
        return {"ok": False, "unverified": False,
                "reason": "hasil kosong atau berisi error",
                "fix": "periksa argumen, lalu coba pendekatan lain"}

    criteria = str(step.get("success") or "").strip()
    if not criteria:
        return {"ok": True, "unverified": True,
                "reason": "tidak ada kriteria — dianggap selesai", "fix": ""}

    prompt = (
        "Nilai apakah satu langkah tugas berhasil. Jawab jujur dan ketat.\n"
        f"LANGKAH: {step.get('title')}\n"
        f"TOOL: {step.get('tool')} {json.dumps(step.get('args') or {}, ensure_ascii=False)[:300]}\n"
        f"KRITERIA BERHASIL: {criteria}\n"
        f"HASIL TOOL (dipotong): {result[:1200]}\n\n"
        "Balas HANYA JSON minified: "
        '{"ok":true,"reason":"alasan singkat","fix":"kalau gagal, apa yang '
        'harus diubah"}'
    )
    try:
        raw = llm(prompt) if llm else _default_llm(prompt)
        data = _extract_json(raw)
        return {"ok": bool(data.get("ok")),
                "unverified": False,
                "reason": str(data.get("reason") or "")[:200],
                "fix": str(data.get("fix") or "")[:200]}
    except Exception as e:                       # noqa: BLE001
        # Model tidak bisa menilai: jangan gagalkan pekerjaan yang mungkin
        # sudah benar — tandai belum terverifikasi.
        return {"ok": True, "unverified": True,
                "reason": f"verifikasi tidak tersedia ({e})", "fix": ""}


def _default_llm(prompt: str) -> str:
    """Teks dari ladder (objek respons → teks), seperti di planner."""
    from core import gemini
    reply = gemini.call(prompt, tier=gemini.FAST, timeout_ms=60000)
    if reply is None:
        return ""
    text = getattr(reply, "text", None)
    if text is None:
        try:
            text = reply.candidates[0].content.parts[0].text
        except Exception:
            text = str(reply)
    return str(text or "")
