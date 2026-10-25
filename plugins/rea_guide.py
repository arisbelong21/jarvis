"""
rea_guide.py — panduan resmi REA (reverse-engineer-anything), on tap.

WHAT REA IS
    REA is not a binary reader: it works on native, managed, Electron/
    JavaScript, packaged, firmware, and browser targets — to explain
    features, compare versions, decompile code, or guide a reconstruction
    of a web, app, or software product from what it shipped. The scope
    stated here is the official skill's own; this file never rewrites it.

WHY THIS EXISTS
    REA ships a skill (SKILL.md v37 + 6 reference guides) that teaches an
    agent how to use its toolset properly. Installing it tells *the agent*
    what to do — and the agent driving REA inside ARIS is the Live model,
    which cannot read files. Without the playbook, ARIS made the mistake
    the guide exists to prevent: it called analysis tools before opening a
    target and got "No app is open" back.

SERVED VERBATIM, NEVER REWRITTEN
    Everything this tool returns is read from the official package —
    SKILL.md plus the six reference guides — at call time. Update the
    package and this tool serves the new text; move or delete it and the
    tool says so plainly instead of failing. Nothing in this file restates
    (and so can never drift from) what REA shipped.
"""
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# The official package lives in skills/ (where the session prompt loads it
# from); the old project-root location is still honoured, so a checkout
# that keeps the package there keeps working.
_SKILL_DIRS = (
    _PROJECT_ROOT / "skills" / "reverse-engineer-anything",
    _PROJECT_ROOT / "reverse-engineer-anything",
)
_SKILL_FILE = "SKILL.md"

# The official guides, by the action names ARIS already knows. Each is read
# verbatim from the package; the Indonesian note is only a label, never a
# substitute for the text.
_REFERENCES = {
    "native":     ("references/native-and-artifacts.md",
                   "binary native, assembly terkelola, arsip, dan ekstraksi"),
    "javascript": ("references/javascript-applications.md",
                   "ASAR, JavaScript/Electron, penelusuran fitur, banding versi"),
    "android":    ("references/android-applications.md",
                   "APK: deklarasi, kelas, method, referensi statis"),
    "runtime":    ("references/runtime-observation.md",
                   "pengamatan browser/Electron pasif, rekonsiliasi statis vs runtime"),
    "evidence":   ("references/evidence-workflows.md",
                   "paging bukti, perbandingan, unknown yang tersisa, verifikasi"),
    "recovery":   ("references/connection-and-recovery.md",
                   "saat tool tidak ada / panggilan diblokir / provider tidak tersedia"),
}
_MAX_CHARS = 6000

# Sections of the official SKILL.md this tool serves, matched by the skill's
# own headings — extracted verbatim, not paraphrased.
_SECTIONS = {
    "route":    "## Route the target first",
    "overview": "## Work summary-first",
}

PLUGIN = {
    "name": "rea_guide",
    "description": (
        "The official REA (reverse-engineer-anything) playbook. REA analyses "
        "native, managed, Electron/JavaScript, packaged, firmware, and browser "
        "targets — to explain features, compare versions, decompile code, or "
        "guide a reconstruction of a web, app, or software product. Call this "
        "BEFORE starting a REA investigation on a new kind of target, or when "
        "a REA call fails and you need the recovery steps. Use for: "
        "'how do I analyse this', 'which tool for this file', 'REA said the "
        "provider is unavailable', 'cara pakai REA', 'panduan REA'. Actions: "
        "'route' (default) the official target-to-tool routing rules, "
        "'overview' the working rules, 'native'/'javascript'/'android'/"
        "'runtime'/'evidence'/'recovery' the matching REA reference guide "
        "verbatim."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "route (default) — which tool opens which kind of target; "
                    "overview — the working rules for a REA investigation; "
                    "native, javascript, android, runtime, evidence, recovery "
                    "— the matching REA reference guide."
                ),
            },
        },
        "required": [],
    },
}

_NO_SKILL = (
    "Paket skill REA (SKILL.md + 6 referensi) tidak ditemukan di "
    "skills/reverse-engineer-anything/ maupun reverse-engineer-anything/. "
    "Aturan inti dari panduan resmi tetap: pilih tool pembuka dari target yang "
    "diberikan — open_binary untuk native/arsip/paket, "
    "analyze_javascript_application untuk ASAR/Electron, "
    "inspect_android_package untuk APK — sebelum memanggil tool analisis."
)


def _skill_dir():
    """Where the official package lives right now, or None."""
    for d in _SKILL_DIRS:
        if (d / _SKILL_FILE).is_file():
            return d
    return None


def _read_skill() -> str:
    d = _skill_dir()
    if d is None:
        return ""
    try:
        return (d / _SKILL_FILE).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _section(text: str, heading: str) -> str:
    """Body of one '## ' section, verbatim. Empty when the heading is absent."""
    start = text.find(heading)
    if start == -1:
        return ""
    start += len(heading)
    nxt = text.find("\n## ", start)
    return text[start:nxt if nxt != -1 else len(text)].strip()


def _cap(text: str, label: str) -> str:
    if len(text) > _MAX_CHARS:
        text = (text[:_MAX_CHARS].rstrip()
                + f"\n\n… (dipotong pada {_MAX_CHARS} karakter; minta lagi kalau "
                  f"bagian selanjutnya perlu)")
    return f"PANDUAN REA — {label}\n\n{text}"


def _read_reference(name: str) -> str:
    rel, desc = _REFERENCES[name]
    d = _skill_dir()
    path = (d / rel) if d else Path(rel)
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return (f"Panduan '{name}' ({desc}) tidak bisa dibaca dari {path}. "
                f"Pastikan folder skills/reverse-engineer-anything/ (atau "
                f"reverse-engineer-anything/) masih ada di proyek.")
    if not text:
        return f"Panduan '{name}' ada tapi kosong."
    return _cap(text, f"{name} ({desc})")


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "route").strip().lower()
    try:
        if action in _SECTIONS:
            skill = _read_skill()
            if not skill:
                # package missing entirely — give the core rules, not a
                # complaint about a heading
                return _NO_SKILL
            text = _section(skill, _SECTIONS[action])
            if text:
                return _cap(text, action)
            # heading renamed upstream — say so, don't invent a substitute
            return (f"Bagian '{_SECTIONS[action]}' tidak ada di SKILL.md paket "
                    f"REA yang terpasang. Paketnya mungkin versi berbeda — cek "
                    f"skills/reverse-engineer-anything/SKILL.md.")
        if action in _REFERENCES:
            return _read_reference(action)
        # a friendly near-miss beats an error: try the first word
        for key in _REFERENCES:
            if key in action:
                return _read_reference(key)
        return (f"Panduan '{action}' tidak ada. Pilihan: route, overview, "
                + ", ".join(_REFERENCES) + ".")
    except Exception as e:
        return f"Sir, the REA guide failed: {e}"
