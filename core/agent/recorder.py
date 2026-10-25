"""
core/agent/recorder.py — menangkap apa yang DITAMPILKAN tool ke layar.

Masalah: sebagian tool (weather_report, flight_finder, beberapa plugin)
menampilkan data ke panel lalu mengembalikan kalimat pendek seperti
"Showing the weather for Jakarta, today, sir.". Agent hanya membaca nilai
kembalian, jadi ia menilai langkah itu gagal — padahal datanya ada, hanya
tidak terlihat olehnya.

Solusi: bungkus `player` dengan perekam. Setiap `show_content(label, text)`
diteruskan ke UI asli (pengguna tetap melihatnya) DAN disimpan. Setelah tool
selesai, isinya digabungkan ke hasil kalau hasil aslinya terlalu pendek untuk
diverifikasi.

Dengan cara ini TIDAK ADA tool yang perlu diubah, dan tool baru otomatis
tercakup selama ia menampilkan hasil lewat `player.show_content`.
"""
from __future__ import annotations

SHORT_RESULT = 200      # hasil di bawah ini dianggap belum memuat data


class ContentRecorder:
    """Proxy `player` yang meneruskan ke UI asli sambil mencatat isinya."""

    def __init__(self, real=None, cap: int = 4000):
        self.real = real          # diset pertama: __getattr__ bergantung padanya
        self.cap = cap
        self.label = ""
        self.text = ""
        self.calls = 0

    # API yang dipakai tool untuk menampilkan hasil
    def show_content(self, label: str, text: str) -> None:
        self.calls += 1
        self.label = str(label or "")[:80]
        self.text = str(text or "")[: self.cap]
        if self.real is not None:
            try:
                self.real.show_content(label, text)
            except Exception:
                pass

    # Atribut lain (current_file, muted, …) diteruskan apa adanya supaya
    # plugin yang memeriksa keadaan UI tetap bekerja seperti biasa.
    def __getattr__(self, name):
        real = object.__getattribute__(self, "real")
        if real is None:
            raise AttributeError(name)
        return getattr(real, name)


def merge_result(result: str, rec: ContentRecorder | None) -> str:
    """Gabungkan hasil tool dengan isi yang ditampilkan, bila perlu.

    Hanya dilakukan kalau hasil aslinya pendek — hasil yang sudah panjang
    (mis. web_search) tidak perlu ditambah, dan menambahkannya hanya membuat
    verifikasi lebih mahal tanpa manfaat.
    """
    text = str(result or "").strip()
    if rec is None or not rec.text:
        return text or "Done."
    if len(text) >= SHORT_RESULT:
        return text
    head = text or "(tanpa keterangan)"
    label = f" [{rec.label}]" if rec.label else ""
    return f"{head}\n\n[ISI YANG DITAMPILKAN KE PENGGUNA{label}]\n{rec.text}"
