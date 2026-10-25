"""
core/semantic.py — memori yang bisa DICARI, bukan hanya dibaca berurutan.

Masalah yang diselesaikan: long_term.json menyimpan fakta sebagai daftar, dan
seluruh isinya ikut masuk prompt. Setelah beberapa minggu itu jadi mahal dan
tidak fokus — yang relevan tenggelam di antara yang tidak.

Di sini setiap ingatan disimpan satu baris JSONL dan dicari dengan **BM25**
(ranking teks klasik: frekuensi kata × keunikan kata × normalisasi panjang),
ditambah sedikit bonus kebaruan. Tanpa model embedding, tanpa API, tanpa
dependensi baru — deterministik dan bisa diuji. Kalau nanti ingin kualitas
pencarian yang lebih baik, fungsi search() bisa diganti embedding tanpa
mengubah pemanggilnya.
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "memory" / "semantic.jsonl"
MAX_ENTRIES = 4000          # batas keras; yang paling lama dibuang

_STOP = {
    "yang", "dan", "di", "ke", "dari", "untuk", "dengan", "itu", "ini", "saya",
    "aku", "kamu", "anda", "nya", "ada", "adalah", "pada", "juga", "akan",
    "sudah", "belum", "bisa", "tidak", "ya", "yaitu", "atau", "karena", "kalau",
    "the", "a", "an", "of", "to", "in", "on", "for", "with", "is", "are", "was",
    "and", "or", "it", "this", "that", "my", "me", "you", "we", "i",
}


def _tokens(text: str) -> list[str]:
    """Kata dasar kasar: huruf kecil, buang imbuhan umum Indonesia/Inggris."""
    raw = re.findall(r"[a-zA-ZÀ-ÿ0-9]+", (text or "").lower())
    out = []
    for w in raw:
        if len(w) < 2 or w in _STOP:
            continue
        for suf in ("kannya", "annya", "nya", "kah", "lah", "kan", "an",
                    "ing", "ed", "es", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


def _read() -> list[dict]:
    try:
        lines = PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    items = []
    for line in lines:
        try:
            d = json.loads(line)
            if isinstance(d, dict) and d.get("text"):
                items.append(d)
        except Exception:
            continue
    return items


def add(text: str, tags: list[str] | None = None, source: str = "") -> str:
    """Simpan satu ingatan. Mengembalikan id-nya."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    mid = uuid.uuid4().hex[:12]
    rec = {"id": mid, "text": text[:1200],
           "tags": [str(t)[:24] for t in (tags or [])][:8],
           "source": str(source)[:40],
           "ts": time.time()}
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _trim()
    except Exception as e:                       # noqa: BLE001
        print(f"[Memory] gagal menyimpan: {e}")
    return mid


def _trim() -> None:
    """Buang yang paling lama kalau melewati MAX_ENTRIES."""
    try:
        if PATH.stat().st_size < MAX_ENTRIES * 220:
            return
        items = _read()
        if len(items) <= MAX_ENTRIES:
            return
        keep = sorted(items, key=lambda d: d.get("ts", 0))[-MAX_ENTRIES:]
        PATH.write_text("\n".join(json.dumps(d, ensure_ascii=False)
                                  for d in keep) + "\n", encoding="utf-8")
    except Exception:
        pass


def search(query: str, k: int = 4) -> list[dict]:
    """BM25 + bonus kebaruan. Mengembalikan [{text, score, id}] teratas."""
    items = _read()
    q = _tokens(query)
    if not items or not q:
        return []

    docs = [(_tokens(d["text"]), d) for d in items]
    n = len(docs)
    avg_len = sum(len(t) for t, _ in docs) / max(1, n)

    df: dict[str, int] = {}
    for toks, _ in docs:
        for w in set(toks):
            df[w] = df.get(w, 0) + 1

    k1, b = 1.5, 0.75
    now = time.time()
    scored = []
    for toks, rec in docs:
        if not toks:
            continue
        score = 0.0
        tf: dict[str, int] = {}
        for w in toks:
            tf[w] = tf.get(w, 0) + 1
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            freq = tf[term]
            score += idf * (freq * (k1 + 1)) / (
                freq + k1 * (1 - b + b * len(toks) / max(1.0, avg_len)))
        if score <= 0:
            continue
        # bonus kebaruan: 0,15 untuk yang paling baru, menurun dalam 30 hari
        age_days = max(0.0, (now - float(rec.get("ts", now))) / 86400.0)
        score *= 1.0 + 0.15 * math.exp(-age_days / 30.0)
        scored.append((score, rec))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, rec in scored[:k]:
        out.append({"id": rec.get("id"), "text": rec.get("text"),
                    "score": round(score, 3), "ts": rec.get("ts")})
    return out


def context(query: str, k: int = 4, max_chars: int = 700,
            min_score: float = 0.0) -> str:
    """Blok siap-tempel untuk prompt: '- ingatan 1\\n- ingatan 2'.

    `min_score` menyaring kecocokan lemah. Tanpa ambang, satu kata yang
    kebetulan sama sudah cukup untuk menyelipkan ingatan yang tidak relevan —
    dan model lalu mengangkatnya sebagai topik.
    """
    hits = [h for h in search(query, k=k) if h["score"] >= min_score]
    if not hits:
        return ""
    lines, used = [], 0
    for h in hits:
        t = h["text"]
        if used + len(t) > max_chars:
            t = t[: max(0, max_chars - used)]
        lines.append(f"- {t}")
        used += len(t)
        if used >= max_chars:
            break
    return "\n".join(lines)


def stats() -> dict:
    items = _read()
    return {"entries": len(items),
            "bytes": PATH.stat().st_size if PATH.exists() else 0}
