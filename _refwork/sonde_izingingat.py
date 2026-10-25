# -*- coding: utf-8 -*-
"""Probe Fase 4 Slice 3 — izin per-tool yang DIINGAT, storage terpisah.

MERAH sebelum implementasi (harus gagal terhadap kode SEKARANG):
  1. core.tool_trust belum ada (penyimpanan memory/tool_trust.json).
  2. permissions.decide belum menaikkan confirm -> allow dari trust
     (dan belum punya jalur args_hash).
  3. main.py belum punya cabang "selalu izinkan" di _confirm_granted,
     belum menyambung grant, belum mem-pass args ke decide.
  4. config belum punya sakelar trust.remember.

Yang di-mock: TIDAK ADA. tool_trust diuji sebagai modul NYATA dengan
berkas NYATA (entri uji dibuat lalu dicabut/dibersihkan); decide() diuji
sebagai fungsi NYATA dengan config nyata; tidak ada stubbing.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")
PERM_SRC = (ROOT / "core" / "agent" / "permissions.py").read_text(encoding="utf-8")

errs = []


def check(name, ok, detail=""):
    print(f"  {name}: {detail} => {'OK' if ok else 'FAIL'}")
    if not ok:
        errs.append(name)


# ── 1. modul tool_trust ──────────────────────────────────────────────────────
_PROBE = "sonde_trust_probe"
try:
    from core import tool_trust
except Exception as e:                                   # noqa: BLE001
    print(f"  import tool_trust: {type(e).__name__}: {e} => FAIL")
    errs.append("import tool_trust")
else:
    check("modul: path di memory/tool_trust.json",
          "tool_trust.json" in str(tool_trust.TRUST_PATH)
          and "memory" in str(tool_trust.TRUST_PATH),
          str(tool_trust.TRUST_PATH))
    check("modul: TIDAK menyentuh api_keys.json",
          "api_keys" not in str(tool_trust.TRUST_PATH))

    # Uji nyata di berkas NYATA: entri probe dibuat, diuji, lalu dicabut dan
    # berkas dikosongkan lagi supaya tak ada sisa.
    try:
        tool_trust.grant(_PROBE, level="always", granted_by="sonde")
        _e = tool_trust.evaluate(_PROBE)
        check("grant -> evaluate ditemukan", bool(_e), str(_e))
        check("entri level always",
              bool(_e) and _e.get("level") == "always")
        check("entri args_hash null (tool utuh)",
              bool(_e) and _e.get("args_hash") in (None, ""))
        check("entri scope this_machine",
              bool(_e) and _e.get("scope") == "this_machine")
        check("entri granted_by terekam",
              bool(_e) and _e.get("granted_by") == "sonde")
        check("entri granted_at ISO-8601",
              bool(_e) and "T" in str(_e.get("granted_at") or ""))
        check("entri uses mulai 0",
              bool(_e) and int(_e.get("uses") or 0) == 0)

        tool_trust.record_use(_PROBE)
        _e2 = tool_trust.evaluate(_PROBE)
        check("uses bertambah setelah dipakai",
              bool(_e2) and int(_e2.get("uses") or 0) == 1,
              str(_e2.get("uses")))

        # args_hash: dua argumen berbeda harus beda nasib.
        tool_trust.grant(_PROBE, level="always",
                         args={"to": "raka@contoh.id"}, granted_by="sonde")
        check("args sama -> masih dipercaya",
              bool(tool_trust.evaluate(_PROBE, {"to": "raka@contoh.id"})))
        check("args beda -> TIDAK dipercaya",
              not tool_trust.evaluate(_PROBE, {"to": "orang_lain@contoh.id"}))
        check("args tak diketahui -> TIDAK dipercaya",
              not tool_trust.evaluate(_PROBE, None))
        tool_trust.grant(_PROBE, level="always", granted_by="sonde")

        # once: satu pakai, lalu hangus.
        tool_trust.grant(_PROBE, level="once", granted_by="sonde")
        check("once: pakai pertama dipercaya",
              bool(tool_trust.evaluate(_PROBE)))
        check("once: pakai kedua hangus",
              not tool_trust.evaluate(_PROBE))

        # kedaluwarsa.
        tool_trust.grant(_PROBE, level="always", granted_by="sonde",
                         expires_at="2000-01-01T00:00:00")
        check("kedaluwarsa -> tidak dipercaya",
              not tool_trust.evaluate(_PROBE))

        # pencabutan: entri tetap ada (audit), tapi tidak berlaku.
        tool_trust.grant(_PROBE, level="always", granted_by="sonde")
        tool_trust.revoke(_PROBE)
        check("revoke -> tidak dipercaya",
              not tool_trust.evaluate(_PROBE))
        _raw = json.loads(tool_trust.TRUST_PATH.read_text(encoding="utf-8"))
        check("revoke -> entri disimpan untuk audit",
              _PROBE in (_raw.get("tool_trust") or _raw)
              and (_raw.get("tool_trust") or {})[_PROBE].get("revoked") is True)

        # destructive TIDAK PERNAH bisa di-trust: grant MENOLAK, dan
        # evaluate juga menolak (berkas bisa disunting tangan).
        for _dt in ("shutdown_jarvis", "undo"):
            _ditolak = False
            try:
                tool_trust.grant(_dt, level="always", granted_by="sonde")
            except ValueError:
                _ditolak = True
            check(f"destructive {_dt} ditolak saat grant", _ditolak)
            check(f"destructive {_dt} tak bisa di-trust (evaluate)",
                  not tool_trust.evaluate(_dt))
            tool_trust.revoke(_dt)

        # tool lain tak terpengaruh.
        check("tool lain tak terpengaruh",
              not tool_trust.evaluate("web_search"))
    finally:
        # Bersihkan: buang probe + destructive dari berkas nyata.
        try:
            _raw = json.loads(tool_trust.TRUST_PATH.read_text(encoding="utf-8"))
            _t = _raw.get("tool_trust") or _raw
            for _k in (_PROBE, "shutdown_jarvis", "undo"):
                _t.pop(_k, None)
            tool_trust.TRUST_PATH.write_text(
                json.dumps({"tool_trust": _t}, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as e:                           # noqa: BLE001
            print(f"  bersih-bersih: {type(e).__name__}: {e}")

    check("modul: grant punya parameter level/expires_at/args",
          all(p in str(__import__("inspect").signature(
              tool_trust.grant).parameters)
              for p in ("level", "expires_at", "args", "granted_by")))
    check("modul: evaluate menerima args",
          "args" in str(__import__("inspect").signature(
              tool_trust.evaluate).parameters))

# ── 2. decide() menaikkan confirm -> allow dari trust ────────────────────────
try:
    from core.agent import permissions

    _t = "sonde_trust_probe"
    from core import tool_trust
    # Kontrol DULU: tanpa trust, keputusan harus confirm (aturan dasar).
    _d0, _w0 = permissions.decide(_t, {}, known=False)
    check("kontrol: tanpa trust -> confirm", _d0 == "confirm", _w0)
    tool_trust.grant(_t, level="always", granted_by="sonde")
    try:
        _d2, _w2 = permissions.decide(_t, {}, known=False)
        check("tool dipercaya -> allow", _d2 == "allow", _w2)
        check("angkatannya dari trust", "tool_trust" in _w2, _w2)
        # deny TIDAK PERNAH bisa dikalahkan trust.
        _d3, _w3 = permissions.decide(
            _t, {"tool_permissions": {"deny": [_t]}}, known=False)
        check("deny menang atas trust", _d3 == "deny", _w3)
        # confirm eksplisit di config juga dilift trust (deny > trust > lainnya).
        _d4, _w4 = permissions.decide(
            _t, {"tool_permissions": {"confirm": [_t]}}, known=False)
        check("trust menang atas confirm eksplisit", _d4 == "allow", _w4)
        # auto_allow lama tetap allow.
        _d5, _ = permissions.decide(
            "web_search", {"tool_permissions": {"auto_allow": ["web_search"]}},
            known=True)
        check("auto_allow lama tetap jalan", _d5 == "allow")
        # tool umum lain tetap seperti dulu.
        _d6, _ = permissions.decide("send_message", {}, known=True)
        check("tool external lain tetap confirm", _d6 == "confirm")
        _d7, _ = permissions.decide("recall_memory", {}, known=True)
        check("read-only lain tetap allow", _d7 == "allow")
        # args_hash diperhatikan decide().
        tool_trust.grant(_t, level="always", args={"to": "raka@contoh.id"},
                         granted_by="sonde")
        _d8, _ = permissions.decide(_t, {}, known=False, args={"to": "raka@contoh.id"})
        check("decide dengan args sama -> allow", _d8 == "allow")
        _d9, _ = permissions.decide(
            _t, {}, known=False, args={"to": "lain@contoh.id"})
        check("decide dengan args beda -> confirm", _d9 == "confirm")
    finally:
        tool_trust.revoke(_t)
        _raw = json.loads(tool_trust.TRUST_PATH.read_text(encoding="utf-8"))
        (_raw.get("tool_trust") or _raw).pop(_t, None)
        tool_trust.TRUST_PATH.write_text(
            json.dumps({"tool_trust": _raw.get("tool_trust") or {}},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    check("permissions: ada kait tool_trust",
          "tool_trust" in PERM_SRC)
except Exception as e:                                   # noqa: BLE001
    check("permissions: decide + trust", False, f"{type(e).__name__}: {e}")

# ── 3. main.py: cabang "selalu izinkan" ──────────────────────────────────────
check("main: cabang selalu izinkan",
      "selalu" in MAIN_SRC and "tool_trust" in MAIN_SRC
      and "trust.remember" in MAIN_SRC)
check("main: grant dipanggil dari _confirm_granted",
      "tool_trust.grant(" in MAIN_SRC)
# args harus diteruskan ke decide() supaya args_hash bisa dicocokkan —
# diperiksa di pemanggilan decide, bukan di sembarang teks.
# GrokBot 22 Okt: keputusan izin kini lewat pintu pusat
# core/decision_loop.gate(name, args, …), yang meneruskan args ke
# permissions.decide(…, args=args).
_dec_i = MAIN_SRC.find("_decision.gate(")
_dec_blok = MAIN_SRC[_dec_i:_dec_i + 220] if _dec_i >= 0 else ""
_DL_SRC = (ROOT / "core" / "decision_loop.py").read_text(encoding="utf-8")
check("main: args diteruskan ke decide",
      "name, args," in _dec_blok and "args=args" in _DL_SRC,
      _dec_blok.replace("\n", " ")[:110])

# ── 4. config: sakelar ───────────────────────────────────────────────────────
try:
    import config
    check("config: trust.remember ada di default",
          "trust.remember" in config.VOICE_FLAG_DEFAULTS)
    check("config: trust.remember default ON",
          config.VOICE_FLAG_DEFAULTS.get("trust.remember") is True)
except Exception as e:                                   # noqa: BLE001
    check("config: trust.remember", False, f"{type(e).__name__}: {e}")

print()
if errs:
    print(f"ERRORS: {len(errs)}")
    raise SystemExit(1)
print("ERRORS: none")
raise SystemExit(0)
