"""
core/model_switch.py — "ganti model AI jadi GLM 5.3 Flash" → provider + model.

Kenapa dipisah dari main.py: pencocokan nama model itu logika murni, dan
logika murni bisa diuji tanpa PyQt, tanpa config, tanpa jaringan. Yang
tersisa di main.py hanyalah menyimpan pilihan dan membangun ulang otak.

Pencocokan TIDAK boleh kaku. Yang diucapkan orang jarang sama dengan id
model: "GLM 5.3 Flash" vs "zai-org/GLM-5.3-Flash", "Deepseek V4.1 flash
free" vs "deepseek-ai/DeepSeek-V4.1-Flash". Karena itu semua nama
dinormalisasi dulu — huruf kecil, tanpa spasi/tanda hubung/titik/titik dua —
lalu dibandingkan.

Urutan:
    1. sama persis setelah normalisasi
    2. nama provider saja ("river") → model default provider itu
    3. potongan nama ("glm53flash" ⊂ "zai-orgglm53flash")
       — lebih dari satu yang cocok berarti JANGAN menebak: minta dipersempit.
"""
from __future__ import annotations

import re


def _norm(text: str) -> str:
    """Bentuk pembanding: huruf kecil, hanya a-z0-9."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _all_models(providers: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for pid, cfg in (providers or {}).items():
        cfg = cfg if isinstance(cfg, dict) else {}
        seen: list[str] = []
        for m in [cfg.get("model")] + list(cfg.get("models") or []):
            if m and str(m) not in seen:
                seen.append(str(m))
        for m in seen:
            out.append((str(pid), m))
    return out


def resolve(want_model: str, want_provider: str, providers: dict) -> tuple[str, str]:
    """(pid, model) yang dipilih, atau ValueError berisi kalimat untuk pengguna."""
    want_model = (want_model or "").strip()
    want_provider = (want_provider or "").strip().lower()
    providers = providers or {}
    if not providers:
        raise ValueError("Belum ada provider yang dikonfigurasi.")

    ids = list(providers)
    # A provider name that is not one of ours is usually a vendor prefix the
    # model tacked on ("zai-org", "anyprovider"). Ignoring it is friendlier
    # than refusing the whole request — the model name is still resolvable.
    known_provider = want_provider if want_provider in providers else ""

    pairs = _all_models(providers)
    if known_provider:
        pairs = [(p, m) for p, m in pairs if p == known_provider]

    wm = _norm(want_model)

    # 1. persis (setelah normalisasi)
    if wm:
        for pid, model in pairs:
            if _norm(model) == wm:
                return pid, model

    # 2. provider saja
    if known_provider and not wm:
        cfg = providers.get(known_provider) or {}
        model = cfg.get("model") or (cfg.get("models") or [None])[0]
        if model:
            return known_provider, str(model)
        raise ValueError(f"Provider '{known_provider}' belum punya model.")

    # 3. potongan nama
    if wm and len(wm) >= 3:
        hits: list[tuple[str, str]] = []
        for pid, model in pairs:
            if wm in _norm(model) and (pid, model) not in hits:
                hits.append((pid, model))
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            listing = ", ".join(f"{m} ({p})" for p, m in hits)
            raise ValueError(f"Ada beberapa model yang cocok: {listing}. "
                             "Sebutkan lebih spesifik.")

    available = ", ".join(sorted({m for _, m in _all_models(providers)}))
    hint = ""
    if want_provider and not known_provider:
        hint = (f" (provider '{want_provider}' tidak dikenal; yang ada: "
                f"{', '.join(ids)})")
    raise ValueError(
        f"Model '{want_model or '(kosong)'}' tidak dikenal{hint}. "
        f"Tersedia: {available}. Cukup sebut potongannya: 'glm', 'claude', "
        "'deepseek', 'nemotron', atau 'mimo'.")
