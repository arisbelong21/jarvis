"""Satu tempat memutuskan siapa yang mengerjakan satu giliran.

Sebelumnya keputusan ini tersebar di delapan gerbang yang berjalan sendiri-
sendiri di `main.py`:

    classify_turn · is_write_intent · allows_gemini · _mode_command
    _cc_is_heavy · _cc_choice · _is_small_turn · _should_force_brain

Setiap perbaikan menambah satu gerbang, dan gerbang-gerbang itu bisa saling
menimpa — jawaban "pakai otak custom saja" pernah hampir dianggap perintah
mode global. Fungsi ini menyatukan URUTAN keputusan itu di satu tempat, dan
mengembalikan SATU hasil:

    small        mulut (Gemini) menjawab sendiri — 0 token otak
    mode         perintah kendali mode (paksa / default)
    offer_reply  jawaban atas tawaran mouse/keyboard
    force_brain  mode paksa: semua pekerjaan ke otak
    brain        otak custom
    pass         biarkan apa adanya (tool ringan / builtin)

Fungsi ini murni: tidak menyentuh state, tidak memanggil model. Semua
detektor lama dipakai apa adanya — yang berubah hanya urutannya.
"""
from __future__ import annotations

SMALL = "small"
MODE = "mode"
OFFER_REPLY = "offer_reply"
FORCE_BRAIN = "force_brain"
BRAIN = "brain"
PASS = "pass"


def decide_turn(utterance: str, *, name: str = "", is_builtin: bool = False,
                in_brain: bool = False, internal: bool = False,
                force_brain: bool = False, offer_fresh: bool = False,
                vent_mode: bool = False,
                offer_choice: str | None = None, mode_command: str | None = None,
                small_turn: bool = False, write_intent: bool = False,
                gemini_allowed: bool = False) -> dict:
    """Kembalikan {"action": …, "reason": …} — satu keputusan, satu alasan.

    Urutannya sengaja tetap dan eksplisit. Dulu urutan ini tersebar, dan
    itulah sumber "perbaiki satu, muncul satu".
    """
    if internal:
        return {"action": PASS, "reason": "dipanggil internal"}

    if in_brain:
        return {"action": PASS, "reason": "otak sedang bekerja"}

    # 1. Tawaran mouse/keyboard yang masih menggantung menang atas segalanya:
    #    jawaban "pakai otak custom" adalah jawaban tawaran, BUKAN perintah
    #    mode global.
    if offer_fresh and offer_choice in ("brain", "gemini"):
        return {"action": OFFER_REPLY, "reason": f"jawaban tawaran: {offer_choice}"}

    # 2. Perintah kendali mode.
    if mode_command in ("force", "default"):
        return {"action": MODE, "reason": f"perintah mode: {mode_command}"}

    # 2b. MODE CURHAT: pengguna sedang bercerita. Gemini menjawab penuh,
    #     otak TIDAK dipanggil — supaya tidak ada kata di dalam cerita yang
    #     bisa memicu pengalihan. Tool ringan tetap boleh (buka/cek).
    if vent_mode:
        if name in ("brain", "custom_brain"):
            return {"action": SMALL, "reason": "mode curhat aktif"}
        return {"action": PASS, "reason": "mode curhat: tool ringan tetap boleh"}

    # 3. Mode paksa: semua yang bukan builtin dikerjakan otak.
    if force_brain and not is_builtin and name not in ("brain", "custom_brain"):
        return {"action": FORCE_BRAIN, "reason": "mode paksa aktif"}

    # 4. Obrolan ringan: mulut menjawab sendiri (hemat satu panggilan otak).
    if small_turn and name in ("brain", "custom_brain"):
        return {"action": SMALL, "reason": "obrolan ringan, bukan tugas"}

    # 5. Permintaan ke otak.
    if name in ("brain", "custom_brain"):
        return {"action": BRAIN, "reason": "permintaan diteruskan ke otak"}

    # 6. Tool ringan yang menyamar sebagai pekerjaan tulis.
    if (write_intent and not is_builtin and not gemini_allowed):
        return {"action": BRAIN, "reason": "niat menulis, bukan tugas ringan"}

    return {"action": PASS, "reason": "boleh ditangani langsung"}
