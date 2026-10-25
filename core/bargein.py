# -*- coding: utf-8 -*-
"""core/bargein.py — apa yang hilang saat pengguna memotong JARVIS.

Fase 4. Dua fakta lenyap setiap kali sebuah giliran dipotong (main.py
membuang in_buf dan out_buf saat `self._interrupted`):

  1. apa yang JARVIS sedang katakan ketika dipotong — ucapan terpotong di
     tengah kalimat, dan tidak ada yang mencatat di mana;
  2. kata-kata pengguna yang memotong ("tunggu, maksudku yang kemarin") —
     dibuang bersama sisa giliran.

Akibatnya giliran berikutnya mulai buta: JARVIS bisa mengulang dari awal
ucapan yang sudah terucap sebagian, atau menjawab seolah ucapan pemotong
tidak pernah ada — persis keluhan "dia mengulang lagi dari awal".

Modul ini sengaja hanya FUNGSI MURNI: mengambil ekor transkrip dan
menyusun catatan sekali pakai [PUTUSAN_TADI]. Penyimpanan (self._cut_note)
dan penyuntikan ke sesi Live hidup di main.py — di sana butuh sesi.
"""

CUT_TAG = "[PUTUSAN_TADI]"
CUT_TAIL_MAX = 400     # karakter ekor ucapan JARVIS yang diingat
CUT_PARTIAL_MAX = 200  # karakter ucapan pemotong yang diingat


def cut_tail(text, limit: int = CUT_TAIL_MAX) -> str:
    """Ekor transkrip: bagian TERAKHIR yang diingat, bukan semuanya.

    Yang dibutuhkan giliran berikutnya adalah "di mana ia berhenti", jadi
    ekor — bukan awal. Whitespace dirapikan dulu supaya batas karakter
    tidak habis untuk spasi."""
    return str(text or "").strip()[-limit:].strip()


def compose_cut_note(tail: str, partial: str) -> str:
    """Catatan sekali pakai: pengguna memotong di titik ini.

    `tail`    — apa yang JARVIS sedang katakan (bisa kosong: dipotong di
                awal ucapan, atau hanya tombol interrupt).
    `partial` — ucapan pengguna yang memotong (bisa kosong).
    Tanpa satu pun fakta → "" : menyuntik [PUTUSAN_TADI] kosong hanya
    mengotori konteks."""
    tail = cut_tail(tail)
    partial = " ".join(str(partial or "").split())[:CUT_PARTIAL_MAX].strip()
    if not tail and not partial:
        return ""
    parts = [CUT_TAG]
    if tail:
        parts.append(f"Ucapan assistant terpotong pengguna di sini: "
                     f"\u201c{tail}\u201d")
    if partial:
        parts.append(f"Yang pengguna katakan saat memotong: "
                     f"\u201c{partial}\u201d")
    parts.append("Lanjutkan dari titik itu: jangan mengulang bagian yang "
                 "sudah terucap, dan jawab ucapan pemotong kalau memang "
                 "membutuhkan jawaban.")
    return "\n".join(parts)
