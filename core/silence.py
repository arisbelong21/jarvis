"""
core/silence.py — the code-side silence gate (Grok's staySilentPlan).

WHY THIS IS CODE
    A voice-first assistant that speaks at the wrong moment is worse than
    one that misses a turn: a wrong silence costs one question, a wrong
    sentence interrupts the user. The decision of whether to speak at all
    therefore does not live in the model's opinion of the moment — it
    lives here, as four ordered conditions over facts the application
    already knows.

THE ORDER (Kandidat C, ADAPTASI-GROK.md)
    1. pengguna baru berbicara        → WAJIB bicara (the user outranks
       everything; they never wait behind a proactive thought)
    2. sudah bicara di siklus ini     → BOLEH diam (two speeches for one
       moment is the spam this gate exists to stop)
    3. ada hasil yang belum disampaikan → WAJIB bicara (owed news is owed;
       a result held silently is a result lost)
    4. ada tanda terima belum diucapkan → WAJIB bicara
    5. selain itu                     → BOLEH diam

    Pure function, no clock, no state: every input is a fact the caller
    already holds, so the same inputs always give the same answer and the
    gate itself can be tested completely offline.
"""
from __future__ import annotations


def stay_silent_plan(they_just_talked: bool, news_owed: bool,
                     receipt_owed: bool,
                     already_spoke: bool) -> tuple[bool, str]:
    """(stay_silent, reason) — should the assistant NOT speak right now?

    The four conditions in their fixed order; the first match decides.
    A False answer means "you must speak" — and the reason says why, so
    the caller (and the log) can see which rule fired.
    """
    if they_just_talked:
        return False, "pengguna baru berbicara — wajib dijawab"
    if already_spoke:
        return True, "sudah berbicara di siklus ini — diam boleh"
    if news_owed:
        return False, "ada hasil yang belum disampaikan — wajib dibicarakan"
    if receipt_owed:
        return False, "ada tanda terima yang belum diucapkan — wajib"
    return True, "tak ada yang menuntut jawaban"
