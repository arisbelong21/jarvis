JARVIS - PETUNJUK SINGKAT
=========================

LANGKAH 1  Klik dua kali: setup.bat
           Semua dikerjakan otomatis:
             - memasang Python 3.13 (kalau belum ada)
             - memasang semua pustaka
             - memasang browser untuk kontrol web
           Butuh internet. Sekali saja, 5-15 menit.

LANGKAH 2  Klik dua kali: start.bat

LANGKAH 3  Saat diminta, isi kunci API:
           - Kunci Gemini  (WAJIB)   https://aistudio.google.com/apikey
           - Kunci provider otak (OPSIONAL)

           Tanpa kunci provider, JARVIS tetap jalan memakai Gemini
           sepenuhnya. Kunci provider baru diperlukan kalau Anda ingin
           "otak custom" untuk tugas berat.

CATATAN
- Semua data (memori, percakapan) disimpan LOKAL di folder ini.
- Tidak ada data yang dikirim ke pembuat aplikasi.
- Untuk menghapus semua memori: hapus berkas memory/long_term.json.
- Mikrofon dan speaker dipilih otomatis. Bisa diubah di pengaturan.

MASALAH UMUM
- "Python tidak bisa dipasang otomatis"
      -> pasang manual dari https://www.python.org/downloads/
         centang "Add python.exe to PATH", lalu jalankan setup.bat lagi.
- "Sebagian pustaka gagal dipasang"
      -> periksa koneksi internet, jalankan setup.bat sekali lagi.
- Browser tidak mau dibuka
      -> jalankan setup.bat lagi (memasang chromium).
- Suara tidak keluar
      -> ganti perangkat output di pengaturan.
