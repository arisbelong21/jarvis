"""Sonde: antrean audio tidak boleh melempar QueueFull ke event loop.

Gejala nyata (23 Okt): konsol dibanjiri
    Exception in callback Queue.put_nowait()
    asyncio.queues.QueueFull
ratusan kali. Sebabnya dua lapis:
  1. main.py:3168 menjadwalkan `out_queue.put_nowait` lewat call_soon_threadsafe
     TANPA penjagaan. out_queue maxsize=200; begitu penuh, setiap frame mic
     melempar QueueFull di dalam callback event loop.
  2. `_send_realtime` berhenti (return) saat koneksi Live tertutup, sehingga
     TIDAK ADA lagi yang menguras antrean — produsen (mic) jalan terus.

Sonde ini menguji dua hal: helper push yang tidak pernah melempar, dan
kelangsungan konsumen setelah koneksi mati.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as mod                                              # noqa: E402

ERRORS = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        ERRORS.append(name)


# ── 1. helper push tidak boleh melempar ────────────────────────────────────
helper = getattr(mod, "_put_drop_oldest", None)
check("helper _put_drop_oldest tersedia", callable(helper),
      "tidak ada" if helper is None else "")

if callable(helper):
    async def fill_and_push():
        q = asyncio.Queue(maxsize=3)
        for i in range(3):
            q.put_nowait(i)
        raised = None
        try:
            helper(q, "baru")          # antrean PENUH
        except BaseException as e:     # noqa: BLE001
            raised = e
        return q, raised

    q, raised = asyncio.run(fill_and_push())
    check("push saat penuh tidak melempar", raised is None,
          type(raised).__name__ if raised else "bersih")
    check("ukuran tetap di batas maxsize", q.qsize() == 3, f"{q.qsize()}")
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    check("frame terbaru tetap masuk", "baru" in items, str(items))
    check("frame tertua dibuang", 0 not in items, str(items))

    # antrean kosong: perilaku normal tidak berubah
    q2 = asyncio.Queue(maxsize=3)
    helper(q2, "x")
    check("antrean kosong: item masuk normal", q2.qsize() == 1, f"{q2.qsize()}")


# ── 2. konsumen tetap hidup setelah koneksi Live mati ─────────────────────
class ConnectionClosedError(Exception):
    """Meniru websockets.exceptions.ConnectionClosedError (lihat log 23 Okt)."""


class _DeadSession:
    """Session yang socketnya sudah tertutup — setiap kirim gagal."""

    def __init__(self):
        self.sent = 0

    async def send_realtime_input(self, **kwargs):
        raise ConnectionClosedError("sent 1011 (internal error) keepalive ping timeout")


async def dead_conn_case():
    live = object.__new__(mod.JarvisLive)
    live.out_queue = asyncio.Queue(maxsize=5)
    live.session = _DeadSession()
    task = asyncio.create_task(live._send_realtime())
    await asyncio.sleep(0.05)
    # produsen menaruh 5 frame; kalau konsumen mati, antrean penuh dan
    # frame berikutnya akan melempar QueueFull
    overflow = None
    for i in range(12):
        try:
            live.out_queue.put_nowait({"data": b"x", "mime_type": "audio/pcm"})
        except asyncio.QueueFull as e:
            overflow = e
        await asyncio.sleep(0.01)
    drained = live.out_queue.qsize()
    alive = not task.done()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    return overflow, drained, alive


overflow, drained, alive = asyncio.run(dead_conn_case())
check("konsumen tetap hidup saat koneksi mati", alive, "task berhenti")
check("antrean tidak meluap setelah koneksi mati", overflow is None,
      type(overflow).__name__ if overflow else "bersih")
check("antrean terkuras (tidak menumpuk)", drained <= 1, f"sisa {drained}")

print()
print("ERRORS:", ERRORS if ERRORS else "none")
sys.exit(1 if ERRORS else 0)
