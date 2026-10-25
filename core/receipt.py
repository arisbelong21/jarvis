# -*- coding: utf-8 -*-
"""core/receipt.py — tanda terima milik APLIKASI, bukan karangan model.

Fase 4 Slice 2. Dua gejala yang diperbaiki di sini:

1. Saat pekerjaan berat dimulai (kirim email, pesan, tugas panjang), kalimat
   penerimaan dulu diucapkan oleh MODEL — dan model bisa mengarang kalimat
   untuk pekerjaan yang belum tentu berjalan, atau mengucapkan penerimaan
   untuk sesuatu yang justru ditolak. Sekarang KODE yang mengucapkannya.

2. Hasil yang benar-benar mendarat dulu hanya hidup di baris HUD dan di
   FunctionResponse — begitu hilang dari konteks, jawaban berikutnya tentang
   "tadi gimana?" menjadi tebakan. Sekarang fakta itu disimpan di cincin
   kecil (RAM) dan bisa disuntikkan ke prompt saat menyambung.

Aturan bentuk (dipakai main.py):
- compose_receipt(...) -> SATU kalimat pendek, bertagar [PEKERJAAN_DITERIMA],
  nama tool TIDAK ikut diucapkan. tag=False untuk jalur otak custom: di sana
  kalimatnya dikembalikan sebagai hasil tool dan pengeras suaranya tetap
  model, jadi tag internal harus dibuang.
- LandedRing -> entri hasil yang sudah mendarat, dedup per id pekerjaan,
  dibatasi LANDED_MAX, blok kosong kalau belum ada apa-apa.
"""

RECEIPT_TAG = "[PEKERJAAN_DITERIMA]"
LANDED_TAG  = "[HASIL_MENDARAT]"
DEFERRED_TAG = "[HASIL_TUNDA]"
LANDED_MAX  = 8     # entri hasil yang diingat (RAM, per sesi)
DEFERRED_MAX = 5    # baris hasil tunda yang ditahan sekaligus
DISPLAY_MAX = 120   # panjang satu baris; lebih panjang dipotong, bukan dibiarkan

_VERDICT_MARK = {"done": "✓", "failed": "✗", "waiting": "…"}


def compose_receipt(title, detail: str = "", tag: bool = True) -> str:
    """Satu kalimat singkat: pekerjaan diterima dan berjalan di latar.

    `detail` menang atas `title` — judul bawaan jalur latar berbentuk
    "nama_tool: ringkasan", dan nama tool bukan bahasa yang layak diucapkan.
    Kalau kedua-duanya kosong, jatuh ke kata netral."""
    text = " ".join(str(detail or title or "").split())
    if not text:
        text = "pekerjaan"
    if len(text) > DISPLAY_MAX:
        text = text[:DISPLAY_MAX - 1].rstrip() + "…"
    return f"{RECEIPT_TAG} {text}" if tag else text


def landed_line(verdict, msg) -> str:
    """Satu baris cincin: penanda verdict + teks hasil apa adanya.

    Baris kosong ditolak (kembalikan "") — cincin hanya boleh memuat fakta,
    bukan placeholder."""
    body = " ".join(str(msg or "").split())
    if not body:
        return ""
    if len(body) > DISPLAY_MAX:
        body = body[:DISPLAY_MAX - 1].rstrip() + "…"
    mark = _VERDICT_MARK.get(str(verdict), "•")
    return f"{mark} {body}"


def deferred_block(lines) -> str:
    """Fase 4 Slice 5 — satu blok hasil yang ditahan selama jendela sunyi.

    Satu blok, bukan sebaris per pekerjaan: lima pengumuman terpisah adalah
    spam dengan bahasa yang berbeda. Baris kosong dibuang; tanpa baris,
    blok kosong — menyuntik tag kosong hanya mengotori konteks."""
    rows = [str(x).strip() for x in (lines or []) if str(x or "").strip()]
    if not rows:
        return ""
    return (DEFERRED_TAG
            + "\nHasil pekerjaan latar yang selesai saat kamu tidak "
              "berbicara:\n" + "\n".join(rows))


class LandedRing:
    """Hasil pekerjaan latar yang sudah mendarat, terakhir di atas.

    Cincin, bukan log: yang dibutuhkan adalah "apa yang baru saja terjadi",
    bukan seluruh riwayat. Dedup per id pekerjaan supaya satu pekerjaan
    tidak dua kali jadi dua fakta, dan yang lama keluar sendiri saat penuh.
    """

    def __init__(self, cap: int = LANDED_MAX):
        self.cap = int(cap)
        self._items: dict[str, str] = {}

    def add(self, job_id, line) -> bool:
        """Tambah entri; False kalau kosong atau id itu sudah ada."""
        line = str(line or "").strip()
        if not line or str(job_id) in self._items:
            return False
        self._items[str(job_id)] = line
        while len(self._items) > self.cap:
            self._items.pop(next(iter(self._items)))
        return True

    def __len__(self) -> int:
        return len(self._items)

    def block(self) -> str:
        """Blok siap-suntik untuk prompt; "" kalau cincin kosong.

        Nilai di-snapshot lebih dulu: add() bisa datang dari thread pekerjaan
        (job selesai) di tengah iterasi, dan dict yang berubah ukuran saat
        diiterasi melempar RuntimeError."""
        if not self._items:
            return ""
        rows = list(self._items.values())
        body = "\n".join(f"- {v}" for v in rows)
        return (f"{LANDED_TAG}\n"
                "Fakta hasil pekerjaan latar yang sudah mendarat. Kalau "
                "pengguna bertanya tentang salah satunya, pakai fakta ini — "
                "jangan menebak, jangan mengarang status.\n" + body)
