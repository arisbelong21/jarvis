import json
import os
import re
import time
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"
_lock            = Lock()
MAX_VALUE_LENGTH = 1200   # 25 Okt — dulu 380. Terlalu pendek untuk cerita:
#                           satu paragraf curhat pun terpotong. Penyimpanan
#                           boleh panjang; yang harus pendek adalah yang
#                           ditampilkan (lihat PROMPT_VALUE_CHARS).
# Berapa karakter nilai yang masuk blok prompt setiap kali connect. Satu fakta
# panjang tidak boleh mendominasi blok yang dibaca setiap panggilan.
PROMPT_VALUE_CHARS = 200
# Berapa karakter nilai yang dikembalikan recall_memory — jalur "baca lebih
# dalam" saat pengguna bertanya, jadi lebih panjang dari prompt tetapi tetap
# dibatasi supaya jawaban tidak dibanjiri satu entri.
RECALL_VALUE_CHARS = 1200

# ── Why there are two very different numbers here ────────────────────────────
#
# There used to be one: MEMORY_MAX_CHARS = 2200, applied to the whole store. It
# was a *storage* limit, and it existed only because the entire memory was
# pasted into the system prompt on every connect — so growing the memory grew
# every single request. When it filled, _trim_to_limit() deleted the oldest
# entries and printed one line to a console nobody reads. A memory described as
# "deeply remembers projects, preferences and personal context" was in practice
# two pages long, and quietly forgot your sister's name after a few weeks.
#
# Storage and prompt budget are now separate concerns:
#
#   MEMORY_MAX_CHARS  — a runaway guard, not a feature limit. Nothing normal
#                       reaches it; a bug writing in a loop does.
#   PROMPT_CORE_CHARS — what actually rides in the system prompt every session.
#                       Smaller than the old whole-memory dump, so sessions
#                       start *faster* than before, not slower.
#
# Everything above the core stays on disk and is fetched on demand by the
# recall_memory tool — see search_memory() and format_memory_for_prompt().
MEMORY_MAX_CHARS  = 200_000
PROMPT_CORE_CHARS = 900
PROMPT_INDEX_CHARS = 420
# Most entries any one category may contribute to the core block, so a person
# with forty stored preferences still gets their sister into the prompt.
PROMPT_MAX_PER_CATEGORY = 6

def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {},
    }

def _atomic_write(path: Path, data: dict, backup: bool = True) -> None:
    """Tulis memori secara ATOMIK: .tmp -> fsync -> os.replace.

    Audit 25 Okt (K1): `write_text` langsung bisa meninggalkan JSON terpotong
    bila proses mati di tengah penulisan. Karena `load_memory` dulu menelan
    kesalahan dan mengembalikan memori kosong, penulisan berikutnya lalu
    MENGHAPUS seluruh ingatan pengguna tanpa pesan.

    `.bak` disimpan lebih dulu supaya selalu ada satu salinan utuh.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    if backup:
        try:
            if path.exists():
                # Audit-2 25 Okt: cadangkan HANYA bila isi lama masih JSON
                # sah. Tanpa syarat ini, berkas yang sudah rusak akan
                # menimpa cadangan yang masih baik — jaring pengamannya hilang
                # tepat saat dibutuhkan.
                json.loads(path.read_text(encoding="utf-8"))
                path.with_suffix(path.suffix + ".bak").write_bytes(
                    path.read_bytes())
        except Exception:                   # noqa: BLE001
            pass                            # cadangan gagal != gagal simpan
    # Windows: berkas bisa sedang dibuka antivirus/indexer -> ulangi sebentar.
    for attempt in range(3):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.05)


def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()
    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for key in base:
                    if key not in data:
                        data[key] = {}
                return data
            raise ValueError("isi berkas bukan objek JSON")
        except Exception as e:                      # noqa: BLE001
            # Audit 25 Okt (K1): JANGAN mengembalikan kosong diam-diam.
            print(f"[Memory] ⚠️ Berkas memori rusak: {e}")
            try:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                corrupt = MEMORY_PATH.with_name(
                    f"{MEMORY_PATH.stem}.corrupt-{stamp}.json")
                MEMORY_PATH.rename(corrupt)
                print(f"[Memory] berkas rusak disimpan sebagai "
                      f"{corrupt.name} — tidak ditimpa")
            except Exception:                       # noqa: BLE001
                pass
            bak = MEMORY_PATH.with_suffix(MEMORY_PATH.suffix + ".bak")
            restored = None
            if bak.exists():
                try:
                    data = json.loads(bak.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        base = _empty_memory()
                        for key in base:
                            if key not in data:
                                data[key] = {}
                        restored = data
                        print("[Memory] ✅ dipulihkan dari cadangan .bak")
                except Exception as e2:             # noqa: BLE001
                    print(f"[Memory] ⚠️ cadangan juga rusak: {e2}")
            if restored is None:
                restored = _empty_memory()
            # Audit-2 25 Okt: berkas rusak sudah DI-RENAME, jadi jalurnya kini
            # kosong. Tanpa menulis balik, panggilan `load_memory` berikutnya
            # mengembalikan memori kosong — pemulihan jadi sia-sia. `backup=
            # False` supaya cadangan yang masih baik tidak tertimpa.
            try:
                _atomic_write(MEMORY_PATH, restored, backup=False)
            except Exception as e3:                 # noqa: BLE001
                print(f"[Memory] ⚠️ gagal menulis hasil pemulihan: {e3}")
            return restored

def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


# Set by main.py so a trim can reach the activity log. Deleting something a
# person told you and mentioning it only on stdout is how a memory loses trust.
_trim_notifier = None


def set_trim_notifier(fn) -> None:
    """Register a callable(str) that surfaces trims to the user."""
    global _trim_notifier
    _trim_notifier = fn


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))
    dropped = []
    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        dropped.append(f"{cat}/{key}")
        print(f"[Memory] 🗑️  Trimmed {cat}/{key}")
    if dropped and _trim_notifier:
        try:
            _trim_notifier(
                f"SYS: Memory full — forgot {len(dropped)} oldest entries "
                f"({', '.join(dropped[:3])}{'…' if len(dropped) > 3 else ''})"
            )
        except Exception:
            pass
    return memory

def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    memory = _trim_to_limit(memory)
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        _atomic_write(MEMORY_PATH, memory)


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val  = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
        print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
    return memory

def _entry_value(entry) -> str:
    """Accept both the {'value': ..., 'updated': ...} shape and a bare string,
    because early versions of the store wrote plain strings."""
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def _pretty(key: str) -> str:
    return key.replace("_", " ").strip()


# Identity is always in the prompt; these categories compete for the remaining
# budget by recency.
_CATEGORY_LABELS = {
    "preferences":   "Preferences",
    "projects":      "Active projects / goals",
    "relationships": "People in their life",
    "wishes":        "Wishes / plans",
    "notes":         "Notes",
}

_IDENTITY_FIELDS = ["name", "age", "birthday", "city", "job",
                    "language", "school", "nationality"]


def format_memory_for_prompt(memory: dict | None) -> str:
    """Build the memory block that goes into the system prompt.

    This used to dump everything. It now sends three things:

      1. IDENTITY  - always, in full. It is small, and it is wrong for the
         assistant to have to look up your name.
      2. RECENT    - the most recently updated entries from every other
         category, up to PROMPT_CORE_CHARS. Recency is the cheapest useful
         relevance signal available without embeddings.
      3. AN INDEX  - the *keys* of everything else, values omitted.

    Point 3 is what makes recall work at all. A model cannot decide to look
    something up if it does not know the thing exists: with only points 1 and 2,
    "who is Ayse?" would get "I don't know" while ayse_sister sat on disk
    unread. The index costs a few hundred characters and turns recall from a
    gamble into a lookup.

    Net effect on latency: this block is SMALLER than the old full dump, so
    every session connects with fewer tokens. Occasionally the model spends one
    extra round trip on recall_memory - covered by the acknowledgment it
    already speaks before any slow step."""
    if not memory:
        return ""

    core_lines: list[str] = []

    # 1. Identity - always, in full
    identity = memory.get("identity", {}) or {}
    for field in _IDENTITY_FIELDS:
        val = _entry_value(identity.get(field))
        if not val:
            continue
        if field == "language":
            # Labelled as an observation, not a setting. A bare "Language:
            # English" line written months ago reads like a standing order and
            # was one of the reasons a Turkish question came back in English.
            core_lines.append(
                f"Has spoken to you in: {val} (an observation about the past — "
                f"always answer in the language of their CURRENT message)")
        else:
            core_lines.append(
                f"{field.title()}: {_clip_summary(val, PROMPT_VALUE_CHARS)}")
    for key, entry in identity.items():
        if key in _IDENTITY_FIELDS:
            continue
        val = _entry_value(entry)
        if val:
            core_lines.append(
                f"{_pretty(key).title()}: "
                f"{_clip_summary(val, PROMPT_VALUE_CHARS)}")

    # 1b. Percakapan terakhir — satu baris saja.
    # 24 Okt: ringkasan sesi selama ini HANYA disimpan, tidak pernah masuk
    # prompt maupun recall, jadi pengguna harus bercerita dari awal setiap
    # sesi baru. Satu baris (≈ 220 char) memberi kontinuitas tanpa biaya
    # besar; sisanya tetap diambil lewat recall_memory bila perlu.
    _sessions = memory.get("sessions") or []
    if isinstance(_sessions, list) and _sessions:
        _last = _sessions[-1]
        _text = (_last.get("summary") if isinstance(_last, dict) else str(_last))
        _text = str(_text or "").strip()
        if _text:
            _day = str(_last.get("date") or "") if isinstance(_last, dict) else ""
            core_lines.append("")
            core_lines.append(
                f"Percakapan terakhir ({_day}): {_clip_summary(_text, 220)}")

    # 2. Everything else, most recently updated first
    rest: list[tuple[str, str, str, str]] = []   # (updated, cat, key, value)
    for cat in _CATEGORY_LABELS:
        for key, entry in (memory.get(cat, {}) or {}).items():
            val = _entry_value(entry)
            if not val:
                continue
            updated = (entry.get("updated", "") if isinstance(entry, dict) else "") or "0000-00-00"
            rest.append((updated, cat, key, val))
    rest.sort(key=lambda t: t[0], reverse=True)

    used    = sum(len(l) + 1 for l in core_lines)
    shown: dict[str, list[str]] = {}
    overflow: dict[str, list[str]] = {}

    # Recency decides order, but no single category may take the whole budget.
    # Without the cap, someone with forty stored preferences gets a prompt that
    # is forty preferences and not one person's name — the categories that
    # matter most in conversation are also the ones that change least often, so
    # pure recency systematically buries them.
    per_cat_used: dict[str, int] = {}
    for _updated, cat, key, val in rest:
        # 25 Okt — satu fakta panjang tidak boleh menghabiskan anggaran
        # prompt. Nilai penuh tetap bisa diambil lewat recall_memory.
        line = (f"  - {_pretty(key).title()}: "
                f"{_clip_summary(val, PROMPT_VALUE_CHARS)}")
        if (per_cat_used.get(cat, 0) < PROMPT_MAX_PER_CATEGORY
                and used + len(line) + 1 <= PROMPT_CORE_CHARS):
            shown.setdefault(cat, []).append(line)
            per_cat_used[cat] = per_cat_used.get(cat, 0) + 1
            used += len(line) + 1
        else:
            overflow.setdefault(cat, []).append(_pretty(key))

    # The index is a table of contents, so it is interleaved across categories
    # rather than continuing in recency order. Sorted by recency it would list
    # twenty-four preferences before the first relationship, and the one entry
    # the index exists for — the old fact the model has no other way to know
    # about — would fall off the end.
    indexed: list[str] = []
    if overflow:
        cats  = [c for c in _CATEGORY_LABELS if overflow.get(c)]
        cursor = {c: 0 for c in cats}
        while cats:
            for cat in list(cats):
                i = cursor[cat]
                if i >= len(overflow[cat]):
                    cats.remove(cat)
                    continue
                indexed.append(overflow[cat][i])
                cursor[cat] = i + 1

    for cat, label in _CATEGORY_LABELS.items():
        if shown.get(cat):
            core_lines.append("")
            core_lines.append(f"{label}:")
            core_lines.extend(shown[cat])

    if not core_lines and not indexed:
        return ""

    out = [
        "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]",
        *core_lines,
    ]

    # 3. The index of what is on disk but not in this prompt
    if indexed:
        budget, names = PROMPT_INDEX_CHARS, []
        for n in indexed:
            if budget - len(n) - 2 < 0:
                break
            names.append(n)
            budget -= len(n) + 2
        if names:
            out.append("")
            out.append(
                "[ALSO REMEMBERED — values not shown here. Call recall_memory "
                "with a keyword to read any of these before saying you do not know]"
            )
            out.append(", ".join(names)
                       + (f" (+{len(indexed) - len(names)} more)"
                          if len(indexed) > len(names) else ""))

    return "\n".join(out) + "\n"


# ── Recall ────────────────────────────────────────────────────────────────────

def _score(query_words: list[str], cat: str, key: str, value: str) -> int:
    """Cheap lexical relevance. No embeddings, no network, no model call - this
    runs in well under a millisecond, which is the entire point: recall must
    cost one model round trip, never two."""
    hay_key = _pretty(key).lower()
    hay_val = value.lower()
    score   = 0
    for w in query_words:
        if not w:
            continue
        if w == hay_key:
            score += 10
        elif w in hay_key:
            score += 6
        if w in hay_val:
            score += 3
        if w in cat:
            score += 1
    return score


def search_memory(query: str, limit: int = 8) -> str:
    """Find stored facts matching `query`. Backs the recall_memory tool.

    An empty query is treated as "show me everything you know", capped - the
    model asks that when the user says "what do you remember about me?"."""
    memory = load_memory()
    # 22 Okt — underscore/hyphen dinormalkan: kunci disimpan sebagai
    # "nama kucing" (prettified) sehingga query "nama_kucing" dulu tidak
    # pernah cocok.
    _q = re.sub(r"[_\-]+", " ", query or "")
    words  = [w for w in re.split(r"[^\w]+", _q.lower()) if len(w) > 1]

    rows: list[tuple[int, str, str, str]] = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue                     # 'sessions' ditangani di bawah
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            s = _score(words, cat, key, val) if words else 1
            if s > 0:
                rows.append((s, cat, key, val))

    # 24 Okt — RINGKASAN SESI ikut dicari. Dulu baris `continue` di atas
    # membuangnya, jadi cerita curhat tersimpan rapi di long_term.json tetapi
    # `recall_memory` tidak pernah bisa menemukannya — persis yang membuat
    # JARVIS menyuruh pengguna "cerita dari awal lagi".
    for e in (memory.get("sessions") or []):
        if not isinstance(e, dict):
            continue
        val = str(e.get("summary") or "").strip()
        if not val:
            continue
        day = str(e.get("date") or "")
        s = _score(words, "sessions", day, val) if words else 1
        if s > 0:
            rows.append((s, "sessions", day, val))

    if not rows:
        return (f"Nothing stored about '{query}'." if query
                else "I have not stored anything about this person yet.")

    rows.sort(key=lambda r: (-r[0], r[2]))
    lines = [(f"session {key}: {_clip_summary(val, RECALL_VALUE_CHARS)}"
              if cat == "sessions"
              else f"{cat}/{_pretty(key)}: {_clip_summary(val, RECALL_VALUE_CHARS)}")
             for _s, cat, key, val in rows[:max(1, limit)]]
    head  = (f"Stored facts matching '{query}':" if query
             else "Everything currently stored:")
    more  = (f"\n(+{len(rows) - len(lines)} more — search with a narrower keyword)"
             if len(rows) > len(lines) else "")
    return head + "\n" + "\n".join(lines) + more


def all_entries_for_ui() -> list[dict]:
    """Flat list for the memory panel: what JARVIS knows, and when it learned it.
    Sorted newest first so the panel opens on what changed most recently."""
    memory = load_memory()
    rows = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            rows.append({
                "category": cat,
                "key":      key,
                "value":    val,
                "updated":  (entry.get("updated", "") if isinstance(entry, dict) else ""),
            })
    rows.sort(key=lambda r: (r["updated"] or "0000-00-00"), reverse=True)
    return rows

def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    cat    = memory.get(category, {})
    if key in cat:
        del cat[key]
        memory[category] = cat
        save_memory(memory)
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


forget_memory = forget


# ── Session memory ─────────────────────────────────────────────────────────────

_SESSION_MAX = 20   # 24 Okt — dulu 3. Tiga sesi terlalu sedikit untuk
#                     "jangan cerita dari awal lagi"; 20 × ~400 char ≈ 8 KB,
#                     dan hanya SATU yang masuk prompt (sisanya lewat
#                     recall_memory), jadi biaya per panggilan tidak naik.
_SESSION_CHARS = 400   # ringkasan sesi boleh lebih panjang dari fakta biasa:
#                        cerita yang dipotong di tengah kalimat tidak berguna.


def _clip_summary(text: str, limit: int) -> str:
    """Potong di batas kata, bukan di tengah kata.

    24 Okt: `summary[:280]` memotong cerita tepat di tengah kata
    ("...dia bilang begitu ke saya kemar"). Ringkasan yang tidak selesai
    lebih buruk daripada ringkasan pendek.
    """
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") + "…"


def save_session_summary(summary: str, language: str = "") -> None:
    """Append a 1-2 sentence session summary to long_term.json['sessions']."""
    summary = (summary or "").strip()
    if not summary:
        return
    memory   = load_memory()
    sessions = memory.get("sessions", [])
    if not isinstance(sessions, list):
        sessions = []
    entry: dict = {
        "date":    datetime.now().strftime("%Y-%m-%d"),
        "summary": _clip_summary(summary, _SESSION_CHARS),
    }
    if language:
        entry["language"] = language
    sessions.append(entry)
    memory["sessions"] = sessions[-_SESSION_MAX:]
    with _lock:
        _atomic_write(MEMORY_PATH, memory)
    print(f"[Memory] 📝 Session saved ({entry['date']}): {summary[:60]}…")


def pop_last_session() -> dict | None:
    """Return the most recent session that has NOT been briefed yet.

    24 Okt — dulu entri DIHAPUS setelah dibacakan sebagai briefing. Akibatnya
    satu-satunya ringkasan curhat pengguna hilang tepat setelah dipakai, dan
    `recall_memory` tidak pernah bisa menemukannya lagi. Sekarang entri
    ditandai `briefed` dan tetap tersimpan: briefing tetap tidak mengulang,
    tetapi ingatannya tidak ikut terbuang.
    """
    with _lock:
        if not MEMORY_PATH.exists():
            return None
        try:
            memory   = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            sessions = memory.get("sessions", [])
            if not isinstance(sessions, list) or not sessions:
                return None
            idx = None
            for i in range(len(sessions) - 1, -1, -1):
                e = sessions[i]
                if isinstance(e, dict) and not e.get("briefed"):
                    idx = i
                    break
            if idx is None:
                return None
            entry = dict(sessions[idx])
            sessions[idx] = {**entry, "briefed": True}
            memory["sessions"] = sessions
            _atomic_write(MEMORY_PATH, memory)
            return entry
        except Exception as e:
            print(f"[Memory] ⚠️ pop_last_session error: {e}")
            return None