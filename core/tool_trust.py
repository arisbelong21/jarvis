"""
core/tool_trust.py — izin per-tool yang DIINGAT, di storage sendiri.

WHY A SEPARATE FILE
    The policy engine (core/agent/permissions.py) has three levels and one
    rule: deny > confirm > auto_allow. What it cannot express is "the user
    already approved THIS tool and does not want to be asked again".
    Writing that into config would mean rewriting the user's config file
    from runtime code — the same file this project keeps off-limits. So the
    memory of an approval lives in memory/tool_trust.json, the same way
    agent state lives in memory/agent_state.json.

THE RULES (koreksi 1.4, ADAPTASI-GROK.md)
    • deny NEVER loses to trust — a tool in tool_permissions.deny stays
      denied no matter how often it was approved before.
    • trust only lifts confirm → allow; it is consulted at the END of
      decide(), after deny/confirm/auto_allow have been checked.
    • expiry is checked on read; a grant whose time has passed is not a
      grant. A grant without an explicit expiry defaults to DEFAULT_TTL_DAYS
      days, so a blanket "always" cannot outlive the context that justified it.
    • destructive tools (shutdown_jarvis, undo — permissions.DESTRUCTIVE) and
      outbound tools (send_message, telegram_remote, … — permissions.EXTERNAL)
      can never be trusted: approving "shut down" or "send this email" once
      must not make every future one automatic.
    • revocation keeps the entry (revoked: true) for the audit trail
      instead of erasing history.

THE SCHEMA (per tool)
    level      "once" (this one decision) | "always"
    scope      "this_machine" — trust is not shared or synced
    args_hash  "sha1:…" | null — null means the whole tool is trusted
    granted_at / expires_at  ISO-8601; expires_at null = no expiry
    granted_by who approved it
    uses       how many times the trust actually let a call through
    revoked    true = withdrawn, kept for audit

WHY evaluate() DOES NOT COUNT USES
    "once" is consumed by the decision itself (evaluate): one approval is
    one decision. "always" counts uses through record_use(), which the
    caller makes when the call actually runs — a decision that was made
    and then cancelled is not a use.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRUST_PATH = ROOT / "memory" / "tool_trust.json"

# Destructive AND outbound tools can never be trusted. Imported lazily from
# the policy engine (one source of truth), with the values inlined as a
# fallback so this module still works if that import ever fails.
# Audit 25 Okt (T-1): kategori EXTERNAL ikut dilarang — "ya, selalu" untuk
# send_message/telegram_remote dulunya mengizinkan pengiriman keluar ke
# tujuan APA PUN tanpa pernah bertanya lagi, dan efeknya tak bisa ditarik.
_DESTRUCTIVE_FALLBACK = {"shutdown_jarvis", "undo"}
_EXTERNAL_FALLBACK = {"send_message", "telegram_remote", "cloud_agent",
                      "calendar_assistant", "meeting_notetaker",
                      "price_tracker"}

# Trust tanpa batas waktu adalah kaki yang tertembak sendiri: satu kalimat
# "ya selalu" berlaku selamanya meski konteksnya sudah berubah. Grant tanpa
# expires_at eksplisit kini kedaluwarsa setelah DEFAULT_TTL_DAYS.
DEFAULT_TTL_DAYS = 7


def _never_trustable() -> set[str]:
    try:
        from core.agent.permissions import DESTRUCTIVE, EXTERNAL
        return ({str(x).strip().lower() for x in DESTRUCTIVE}
                | {str(x).strip().lower() for x in EXTERNAL})
    except Exception:                                   # noqa: BLE001
        return set(_DESTRUCTIVE_FALLBACK) | set(_EXTERNAL_FALLBACK)


def hash_args(args: dict | None) -> str | None:
    """Stable identity of an argument set, or None when there is none.

    Sorted keys so {"a":1,"b":2} and {"b":2,"a":1} are the same call —
    a re-ordering by the model must not invalidate an approval.
    """
    if not args:
        return None
    blob = json.dumps(args, sort_keys=True, ensure_ascii=False)
    return "sha1:" + hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _expired(entry: dict) -> bool:
    raw = str(entry.get("expires_at") or "").strip()
    if not raw:
        return False
    try:
        return datetime.fromisoformat(raw) < datetime.now()
    except ValueError:
        # A malformed expiry is treated as expired: fail closed, the grant
        # simply does not apply and the normal confirmation stands.
        return True


def load() -> dict:
    """The trust table. Missing or broken file → empty (never an error)."""
    try:
        raw = json.loads(TRUST_PATH.read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return {}
    if isinstance(raw, dict):
        tbl = raw.get("tool_trust")
        return dict(tbl) if isinstance(tbl, dict) else (
            {k: v for k, v in raw.items() if isinstance(v, dict)})
    return {}


def save(table: dict) -> None:
    """Atomic write: a crash mid-write must not corrupt the memory."""
    TRUST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRUST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"tool_trust": table}, ensure_ascii=False,
                              indent=2), encoding="utf-8")
    tmp.replace(TRUST_PATH)


def grant(tool: str, level: str = "always", args: dict | None = None,
          granted_by: str = "aris", expires_at: str | None = None,
          scope: str = "this_machine") -> dict:
    """Remember an approval for one tool. Returns the stored entry.

    Destructive and outbound (EXTERNAL) tools are refused outright — the
    entry is not written. Approving a shutdown or an email send once must
    not make all future ones automatic.

    A grant without an explicit expires_at now defaults to DEFAULT_TTL_DAYS
    days (Audit 25 Okt, T-1): a blanket "always" that never lapses is a
    loaded gun, since args_hash=None trusts EVERY argument of that tool.
    """
    t = (tool or "").strip().lower()
    if not t:
        raise ValueError("nama tool kosong")
    if t in _never_trustable():
        raise ValueError(f"tool '{t}' tak bisa di-trust (destruktif/keluar)")
    if level not in ("once", "always"):
        level = "always"
    if not expires_at:
        expires_at = (datetime.now()
                      + timedelta(days=DEFAULT_TTL_DAYS)).isoformat(
                          timespec="seconds")
    table = load()
    entry = {
        "level": level,
        "scope": scope or "this_machine",
        "args_hash": hash_args(args),
        "granted_at": _now_iso(),
        "expires_at": expires_at,
        "granted_by": granted_by or "aris",
        "uses": 0,
        "revoked": False,
    }
    table[t] = entry
    save(table)
    return dict(entry)


def revoke(tool: str) -> None:
    """Withdraw a trust. The entry stays, revoked: true, for the audit."""
    t = (tool or "").strip().lower()
    table = load()
    if t in table:
        table[t] = dict(table[t])
        table[t]["revoked"] = True
        save(table)


def evaluate(tool: str, args: dict | None = None) -> dict | None:
    """The trust entry if the tool is trusted for THIS call, else None.

    Pure read for "always" (uses are counted by record_use). For "once"
    the decision IS the use: a trusted once-grant is revoked right here,
    so the next call asks again. args=None with an args_hash grant is
    NOT trusted — an approval tied to specific arguments cannot be
    verified against unknown ones.
    """
    t = (tool or "").strip().lower()
    entry = load().get(t)
    if not isinstance(entry, dict) or entry.get("revoked"):
        return None
    if t in _never_trustable():
        # Lapis kedua: berkas bisa disunting tangan. Tool destruktif tak
        # pernah dipercaya, entri sebagai apa pun keadaannya.
        return None
    if _expired(entry):
        return None
    want = entry.get("args_hash")
    if want and hash_args(args) != want:
        return None
    if entry.get("level") == "once":
        table = load()
        table[t] = dict(entry)
        table[t]["revoked"] = True
        save(table)
    return dict(entry)


def is_trusted(tool: str, args: dict | None = None) -> bool:
    """Convenience for the policy engine's confirm → allow lift."""
    return evaluate(tool, args) is not None


def record_use(tool: str) -> None:
    """Count a use: the trust let a real call through."""
    t = (tool or "").strip().lower()
    table = load()
    entry = table.get(t)
    if isinstance(entry, dict) and not entry.get("revoked"):
        table[t] = dict(entry)
        table[t]["uses"] = int(entry.get("uses") or 0) + 1
        table[t]["last_used_at"] = _now_iso()
        save(table)
