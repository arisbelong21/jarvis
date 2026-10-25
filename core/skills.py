"""
skills.py — folders of instructions JARVIS carries into every session.

WHY A FOLDER AND NOT A PLUGIN
    A plugin adds a TOOL: the model has to decide to call it, which it only does
    if it already suspects it needs help. A skill adds KNOWLEDGE: the workflow
    rules that span several tools, which is exactly what no single tool
    description can carry. REA is the case in point — its 207 tool descriptions
    each explain one call, and none of them says "open the target before you
    analyse it". That rule is what a skill is for.

    So this is the JARVIS counterpart of what Hermes does with the same folder:
    a skill dropped into skills/<name>/SKILL.md is read at startup and folded
    into the system prompt. Install by copying a folder; nothing else to wire.

WHAT GETS INJECTED, AND WHY NOT ALL OF IT
    The prompt file itself asks to be kept short, and it is right: text that
    repeats a tool description competes for attention with the rules that are
    written nowhere else. So a skill contributes its JARVIS BRIEF section when
    it has one — the author's own summary of what belongs in the prompt — and
    otherwise a trimmed head of the body. There is a hard per-skill and total
    budget; a skill can never crowd out the conversation.

    The full manual stays available on demand (see plugins/rea_guide.py, which
    serves the same folder's references), so nothing is lost by keeping the
    prompt small.
"""
from __future__ import annotations

from pathlib import Path

_MAX_PER_SKILL = 2000      # characters of a single skill that reach the prompt
_MAX_TOTAL = 6000          # characters for all skills together
_BRIEF_MARKERS = ("## JARVIS BRIEF", "## BRIEF")


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """(metadata, body). A malformed header is ignored, never fatal."""
    meta: dict = {}
    if not text.startswith("---"):
        return meta, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return meta, text
    for line in parts[1].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip()
    return meta, parts[2].lstrip("\n")


def _brief_of(body: str, meta: dict) -> str:
    """The part of a skill that belongs in the prompt."""
    for marker in _BRIEF_MARKERS:
        idx = body.find(marker)
        if idx != -1:
            return body[idx + len(marker):].strip()
    return body.strip()


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if "\n" in cut[limit // 2:]:
        cut = cut[:cut.rfind("\n")]
    return cut.rstrip() + "\n… (dipotong — detailnya ada di tool yang menyertainya)"


def load_skills(skills_dir: Path) -> tuple[str, list[dict]]:
    """(prompt block, manifest). Never raises; a missing folder is simply empty."""
    skills_dir = Path(skills_dir)
    try:
        candidates = sorted(p for p in skills_dir.iterdir() if p.is_dir())
    except Exception:
        return "", []

    blocks: list[str] = []
    manifest: list[dict] = []
    used = 0
    for folder in candidates:
        path = folder / "SKILL.md"
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        meta, body = _parse_front_matter(raw)
        name = meta.get("name") or folder.name
        brief = _brief_of(body, meta)
        if not brief:
            continue
        room = max(0, min(_MAX_PER_SKILL, _MAX_TOTAL - used))
        if room < 200:
            break
        brief = _trim(brief, room)
        used += len(brief)
        header = f"[SKILL: {name}]"
        desc = meta.get("description")
        if desc:
            header += f"\n{desc}"
        blocks.append(f"{header}\n\n{brief}")
        manifest.append({"name": name, "path": str(path), "chars": len(brief)})

    if not blocks:
        return "", []
    intro = ("[SKILLS — cara kerja yang berlaku lintas tool. Ikuti ini; "
             "deskripsi tiap tool tetap yang menentukan detail pemanggilannya.]")
    return intro + "\n\n" + "\n\n".join(blocks), manifest
