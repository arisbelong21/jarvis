"""
core/agent/planner.py — pecah permintaan menjadi langkah yang bisa diperiksa.

Kenapa perlu: otak biasa menerima tugas lalu memanggil tool satu per satu
sambil berharap hasilnya benar. Tanpa rencana, tidak ada yang bisa diverifikasi
— dan kegagalan baru terlihat di akhir, saat semuanya sudah terlanjur.

Rencana di sini punya bentuk tetap supaya bisa dieksekusi dan diperiksa mesin:

    {"goal": "...",
     "steps": [{"id": 1, "title": "...", "tool": "docx_writer",
                "args": {...}, "success": "kriteria yang bisa dinilai",
                "risk": "read|write|external"}]}

`success` bukan hiasan: verifier memakainya untuk memutuskan langkah itu
berhasil atau harus diulang. Kalau model tidak bisa menuliskan kriteria yang
jelas, langkah itu memang belum siap dijalankan.
"""
from __future__ import annotations

import json
import re

MAX_STEPS = 10


def _extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def _tool_catalog(tools: list[str], limit: int = 60) -> str:
    names = [t for t in tools if t][:limit]
    return ", ".join(names)


def make_plan(task: str, tools: list[str], llm=None,
              max_steps: int = MAX_STEPS) -> dict:
    """Buat rencana. `llm(prompt) -> str` bisa diganti stub saat diuji."""
    task = (task or "").strip()
    if not task:
        raise ValueError("tugas kosong")

    prompt = (
        "Kamu perencana tugas untuk asisten komputer. Pecah permintaan pengguna "
        "menjadi langkah-langkah yang masing-masing bisa dijalankan SATU tool "
        "dan bisa diperiksa hasilnya.\n"
        f"PERMINTAAN: {task}\n"
        f"TOOL YANG TERSEDIA: {_tool_catalog(tools)}\n\n"
        "Balas HANYA JSON minified:\n"
        '{"goal":"tujuan akhir satu kalimat","steps":[{"id":1,"title":"apa '
        'yang dilakukan","tool":"nama_tool","args":{},"success":"bagaimana '
        'kita tahu langkah ini berhasil","risk":"read|write|external"}]}\n'
        f"Aturan: maksimal {max_steps} langkah; jangan mengarang nama tool di "
        "luar daftar; kalau tugasnya cukup satu langkah, tulis satu; "
        "'success' harus konkret dan bisa dinilai dari hasil tool."
    )
    raw = llm(prompt) if llm else _default_llm(prompt)
    plan = _extract_json(raw)

    steps = plan.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise ValueError("rencana tidak berisi langkah")
    clean = []
    known = {t.lower() for t in tools}
    for i, s in enumerate(steps[:max_steps], start=1):
        if not isinstance(s, dict):
            continue
        tool = str(s.get("tool") or "").strip()
        if known and tool and tool.lower() not in known:
            # Tool karangan: buang, jangan dijalankan lalu gagal misterius.
            continue
        clean.append({
            "id": i,
            "title": str(s.get("title") or f"Langkah {i}")[:120],
            "tool": tool,
            "args": s.get("args") if isinstance(s.get("args"), dict) else {},
            "success": str(s.get("success") or "")[:160],
            "risk": str(s.get("risk") or "write")[:12],
        })
    if not clean:
        raise ValueError("tidak ada langkah valid (nama tool tidak dikenal?)")
    return {"goal": str(plan.get("goal") or task)[:200], "steps": clean}


def revise_step(step: dict, result: str, fix: str, tools: list[str],
                llm=None) -> dict | None:
    """Ubah langkah yang gagal menjadi langkah baru yang masuk akal.

    Mengulang argumen yang sama hampir selalu gagal dengan cara yang sama —
    itu yang terjadi sebelum fungsi ini ada. Verifier sudah menyebutkan apa
    yang salah (`fix`), jadi di sini model diminta mengubah tool/argumennya,
    bukan mengulanginya.
    """
    prompt = (
        "Satu langkah tugas GAGAL. Ubah langkahnya supaya berhasil.\n"
        f"LANGKAH: {step.get('title')}\n"
        f"TOOL: {step.get('tool')} {json.dumps(step.get('args') or {}, ensure_ascii=False)[:400]}\n"
        f"KRITERIA: {step.get('success')}\n"
        f"HASIL: {result[:800]}\n"
        f"SARAN PERBAIKAN: {fix or '(tidak ada)'}\n"
        f"TOOL TERSEDIA: {_tool_catalog(tools)}\n\n"
        "Balas HANYA JSON satu langkah: "
        '{"title":"...","tool":"...","args":{},"success":"...","risk":"..."}'
        " — atau {\"give_up\":true} kalau memang tidak ada cara yang masuk akal."
    )
    try:
        raw = llm(prompt) if llm else _default_llm(prompt)
        data = _extract_json(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("give_up"):
        return None
    tool = str(data.get("tool") or "").strip()
    known = {t.lower() for t in tools}
    if not tool or (known and tool.lower() not in known):
        return None
    return {
        "id": step.get("id"),
        "title": str(data.get("title") or step.get("title"))[:120],
        "tool": tool,
        "args": data.get("args") if isinstance(data.get("args"), dict) else {},
        "success": str(data.get("success") or step.get("success") or "")[:160],
        "risk": str(data.get("risk") or step.get("risk") or "write")[:12],
    }


def _default_llm(prompt: str) -> str:
    """Teks dari ladder. `gemini.call` mengembalikan objek respons (atau
    _Reply), bukan string — dikonversi di sini supaya pemanggil selalu
    menerima teks."""
    from core import gemini
    reply = gemini.call(prompt, tier=gemini.SMART, timeout_ms=120000)
    if reply is None:
        return ""
    text = getattr(reply, "text", None)
    if text is None:
        try:
            text = reply.candidates[0].content.parts[0].text
        except Exception:
            text = str(reply)
    return str(text or "")
