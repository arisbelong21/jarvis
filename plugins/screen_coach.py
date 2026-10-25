"""
plugins/screen_coach.py — "lihat layar saya dan bimbing".

Ini versi SUARA dari mode share-screen: satu panggilan, satu petunjuk.
Tidak mengklik apa pun — hanya menjelaskan langkah berikutnya dan di mana
elemennya berada. Untuk mode live di HUD (pratinjau + penunjuk), lihat
core/screen_share.py dan panel SHARE SCREEN di ui.py.
"""

import time

PLUGIN = {
    "name": "screen_coach",
    "description": (
        "Melihat layar pengguna dan MEMBIMBING langkah berikutnya — tidak "
        "mengklik apa pun. Gunakan saat pengguna minta 'bimbing saya', "
        "'lihat layar saya, saya mau klik …', 'apa langkah selanjutnya di "
        "layar ini', atau 'saya salah klik, apa yang harus dilakukan'. "
        "Kirim tujuan pengguna apa adanya di 'goal'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "goal": {
                "type": "STRING",
                "description": ("Apa yang ingin dicapai pengguna di layar, "
                                "verbatim."),
            },
            "watch_seconds": {
                "type": "INTEGER",
                "description": ("0 = sekali lihat (default). >0 = amati "
                                "beberapa detik dan beri petunjuk terbaru."),
            },
        },
        "required": ["goal"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    goal = str((parameters or {}).get("goal") or "").strip()
    if not goal:
        return "Sebutkan apa yang ingin bos lakukan di layar."

    try:
        seconds = int((parameters or {}).get("watch_seconds") or 0)
    except Exception:
        seconds = 0
    seconds = max(0, min(30, seconds))

    from core import screen_share

    last_hint = ""
    deadline = time.monotonic() + seconds
    attempts = 0
    while True:
        attempts += 1
        try:
            png, w, h = screen_share.capture()
            res = screen_share.advise(goal, png, w, h, last=last_hint)
        except Exception as e:                       # noqa: BLE001
            return f"Saya tidak bisa melihat layar sekarang: {e}"

        last_hint = res["hint"]
        point = res.get("point")
        if player:
            try:
                player.show_content(
                    "🖥️ BIMBINGAN LAYAR",
                    last_hint
                    + (f"\n\nTunjuk: ({point['x']}, {point['y']})" if point else ""))
            except Exception:
                pass
        print(f"[ScreenCoach] {last_hint}"
              + (f" @({point['x']},{point['y']})" if point else ""))

        if res.get("ok"):
            return f"Tujuan sudah tercapai di layar. {last_hint}"
        if seconds <= 0 or time.monotonic() >= deadline:
            break
        time.sleep(2.5)

    point = res.get("point") if attempts else None
    tail = (f" Elemennya ada di sekitar ({point['x']}, {point['y']})."
            if point else "")
    return f"{last_hint}{tail}"
