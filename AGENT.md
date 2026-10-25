# Lapisan Agent — cara kerja & cara pakai

Dokumen ini menjelaskan lapisan yang membuat Jarvis **mengerjakan tugas sampai
selesai dan memeriksa hasilnya sendiri**, bukan hanya memanggil tool sekali
lalu berharap. Semua yang tertulis di sini sudah diuji (lihat §6).

---

## 1. Lima pilar, dan di mana kodenya

| Pilar | Sebelum | Sekarang | Berkas |
|---|---|---|---|
| **Loop aksi** | 8 langkah, tanpa verifikasi | **12 langkah** (bisa diatur), tiap langkah diverifikasi | `core/custom_brain.py`, `core/agent/runner.py` |
| **Memori** | daftar fakta, dibaca utuh | **BM25 + bonus kebaruan**, hanya yang relevan masuk prompt | `core/semantic.py` |
| **Tool-use** | 46 tool + 207 MCP | + `agent_run`, `reload_plugins` (tool buatan sendiri) | `main.py` |
| **Koreksi diri** | tidak ada | hasil gagal diberi `[PERBAIKI]`; langkah gagal **direvisi**, bukan diulang | `core/custom_brain.py`, `core/agent/planner.py` |
| **Ketahanan** | antrean hilang saat restart | state agent disimpan tiap langkah, bisa dilanjutkan | `memory/agent_state.json` |

---

## 2. Alur satu tugas

```
"kerjakan X sampai selesai"
        │
        ▼
  agent_run  ──► rencana (JSON: langkah + kriteria berhasil)
        │
        ▼
  untuk tiap langkah:
     izin?  deny    → berhenti, alasan jelas
            confirm → PAUSE (state disimpan, bisa dilanjutkan)
            allow   → eksekusi
        │
        ▼
  verifikasi (model menilai kriteria)
     gagal → REVISI langkah (tool/args diubah) → coba lagi (maks 2×)
     gagal terus → berhenti & laporkan jujur (tidak mengaku selesai)
        │
        ▼
  selesai → ringkasan: n/m langkah, berapa yang belum terverifikasi
```

---

## 3. Cara memakai (lewat suara/teks)

| Ucapan | Yang terjadi |
|---|---|
| "Kerjakan sampai selesai: cari data X lalu buat dokumennya" | `agent_run` — rencana, eksekusi, verifikasi |
| "Lanjutkan" | meneruskan tugas yang tadi berhenti minta izin |
| "Muat ulang plugin" | `reload_plugins` — tool baru langsung tersedia |
| "Apa yang kamu lakukan tadi?" | dijawab dari audit trail |

**Kapan TIDAK memakai agent:** satu aksi sederhana ("buka YouTube") cukup
dengan tool langsung — lebih cepat dan lebih murah.

---

## 4. Izin & otonomi (sesuai permintaan bos)

| Kategori | Contoh | Perilaku |
|---|---|---|
| `read` | web_search, system_status, file_processor | **jalan tanpa izin** |
| `mutating` | docx_writer, excel_writer, dev_agent | **jalan** — bos yang memintanya |
| `hands_on` | screen_agent, computer_control | **minta izin** (mengklik di layar bos) |
| `external` | send_message, telegram, composio | **minta izin** (tidak bisa ditarik) |
| `destructive` | shutdown, undo | **minta izin** |
| `unknown` | plugin baru yang belum dikenal | **minta izin** |

Diatur di `config/api_keys.json`:

```json
"tool_permissions": {
    "deny": ["screen_agent"],        // matikan total
    "auto_allow": ["send_message"],  // izinkan tanpa tanya
    "confirm": ["web_search"]        // baca pun minta izin
}
```

Urutan menang: **deny > confirm > auto_allow > kategori bawaan**.

Saat izin diminta, Jarvis **berhenti dan bertanya**; setelah bos menjawab
"ya/lanjut/silakan", panggilan yang sama diterima (berlaku 3 menit).

---

## 5. Audit trail

Setiap pemanggilan tool ditulis ke `memory/audit.jsonl`:

```json
{"ts":"2026-10-21 00:58:12","actor":"agent","tool":"web_search",
 "args":{"query":"…"},"decision":"allow","ok":true,"ms":9010,
 "summary":"Hasil 3 memuat kalimat konkret…"}
```

- Kunci/token **disamarkan** (`"api_key": "***"`)
- Berkas dirotasi otomatis pada 2 MB (`audit.jsonl.1`)
- Dibaca lewat `audit.tail(n)` — bisa dijawab dengan suara

---

## 6. Bukti pengujian

| Uji | Hasil |
|---|---|
| `_refwork/sonde_agent.py` — **23 cek** | **ERRORS: none** |
| `run_tests.py` — 20 berkas | lihat hasil terakhir |
| E2E nyata #1: "cari berita teknologi hari ini" | **failed dengan benar** — verifier menolak landing page tanpa tanggal, setelah 2 revisi |
| E2E nyata #2: "baca requirements.txt, hitung paket" | **failed dengan benar** — tool memotong output, agent menolak mengarang angka |
| E2E nyata #3: "cari satu fakta tentang Jakarta" | **done** — 1/1 langkah, terverifikasi, 9 detik |

Dua kegagalan pertama bukan bug: itu **bukti agent tidak mengaku selesai saat
belum yakin**. Itulah yang diminta: "tugas sampai selesai **tanpa kesalahan**".

---

## 7. Batasan yang jujur

1. **`weather_report` tidak mengambil data apa pun.** Audit menemukan tool ini
   hanya membuka Google di browser lalu mengembalikan "Showing the weather…".
   Agent karena itu menolaknya dengan benar. Kalau ingin tugas cuaca bisa
   diverifikasi, tool-nya harus benar-benar mengambil data (mis. wttr.in).
   — **belum dikerjakan, butuh keputusan bos.**
2. **Antrean persisten** kini mencakup job non-agent juga: `dev_agent` dan
   `code_helper` menyimpan resepnya (`memory/jobs_state.json`) dan ditawarkan
   untuk dilanjutkan saat start. Yang **tidak** otomatis dilanjutkan adalah
   job yang sedang berjalan saat aplikasi mati — dijalankan ulang hanya bila
   bos bilang "lanjutkan", supaya pekerjaan tidak terduplikasi.
3. **Anggaran panggilan model** kini ada: `agent.max_model_calls` (bawaan 24,
   clamp 4–120). Habis → status `budget`, state disimpan, bisa dilanjutkan.
   Yang belum ada: batas **biaya dalam rupiah/dollar** dan batas harian.
4. **Agent tidak bisa memanggil tool built-in** yang butuh event loop
   (`screen_share`, `system_status`). Disengaja: rencana tidak boleh berisi
   langkah yang mustahil dijalankan di latar.
5. **Verifikasi memakai model** — satu panggilan per langkah; ikut terhitung
   dalam anggaran.

---

## 9. Kemajuan yang terlihat di UI (pengguna bertanya "sudah sampai mana?")

Ada tiga tingkatan umpan balik, dan semuanya terlihat tanpa mengganggu:

| Situasi | Yang tampil |
|---|---|
| Tool jalan **langsung** (kirim email, baca berkas) | HUD masuk state `WORKING` + satu baris: `Menjalankan composio_actions…` — bersih lagi begitu selesai |
| Tool ke **latar** (kirim keluar & pekerjaan berat) | baris `Latar: composio_actions: …` plus **penghitung waktu berjalan & fase terakhir** |
| Selesai | diucapkan + masuk activity log: `Pekerjaan latar '…' sudah selesai (12 detik) …` |
| **Gagal** | diucapkan jujur + log: `Pekerjaan '…' GAGAL — <alasan>` — lihat §10 |
| Menunggu konfirmasi | diucapkan sebagai pertanyaan ya/tidak — belum dijalankan |

### Aturan tool yang dijalankan ke latar

```python
_LONG_TOOLS = {"send_message", "composio_actions", "cloud_agent",
               "telegram_remote"} | _BACKGROUND_TOOLS   # dev_agent, code_helper, …
```

Untuk `composio_actions`, hanya aksi yang **mengirim** — `status`, `list`,
`get`, `read`, `find`, `search`, `check` tetap jalan langsung karena cepat;
mengantreinya justru membuat jawaban jauh lebih lambat.

Baris kemajuan untuk tool langsung **tidak dipakai** saat giliran dipegang otak
custom (`self._in_brain`), karena otak itu punya barisnya sendiri
("Otak custom: memahami permintaan… / menjalankan …"). Dua-duanya tidak
pernah berebut baris yang sama.

### Tahap 1 — anggaran panggilan model
`AgentRunner(max_calls=…)`, dihitung dengan membungkus fungsi model
(`_counted`), bukan ditebak. `BudgetExceeded` sengaja turunan
**BaseException** — kalau turunan Exception, planner/verifier menelannya dan
tugas melewati batas biaya tanpa terlihat.

### Tahap 2 — isi yang ditampilkan ke UI
`core/agent/recorder.py`: `ContentRecorder` membungkus `player`, meneruskan
`show_content` ke UI asli sambil menyimpan isinya; `merge_result()` menambahkan
isi itu bila hasil tool < 200 karakter. **15 plugin** memakai `show_content`,
jadi semuanya ikut terbantu tanpa diubah satu per satu.

### Tahap 3 — antrean persisten
`JobRunner.submit(..., descriptor=…)` + `persist_path`; resep job ditulis ke
`memory/jobs_state.json` selama queued/running dan dihapus saat selesai.
Saat start, Jarvis menyebutkan pekerjaan yang terputus dan menunggu
"lanjutkan".

### Bug yang ditemukan audit (dan sudah diperbaiki)

| Bug | Dampak | Perbaikan |
|---|---|---|
| `save_memory`, `agent_run`, `reload_plugins` tidak terdaftar di kategori | ketiganya jatuh ke `unknown` → **selalu minta izin** | ditambahkan ke `MUTATING` |
| `BudgetExceeded` turunan `Exception` | ditelan `except Exception` di verifier → anggaran tidak pernah berlaku | dijadikan turunan `BaseException` |
| Diagnosis awal "weather_report menampilkan data ke UI" | salah — tool itu hanya membuka browser | dicatat di §7 sebagai pekerjaan terpisah |

### Dampak yang diperiksa (tidak ada regresi)

| Pemeriksaan | Hasil |
|---|---|
| `JobRunner.submit` tanpa descriptor (pemanggil lama) | tetap jalan |
| `JobRunner` tanpa `persist_path` | tidak menulis berkas apa pun |
| Perekam meneruskan atribut UI lain (`current_file`, `muted`) | diteruskan |
| Hasil tool panjang tidak ditambahi isi UI | tidak diubah |
| Memori semantik dengan berkas rusak | tidak crash, bisa menulis lagi |
| Semua tool bawaan: keputusan izin | sesuai harapan (13 allow, 3 confirm) |
| Sinyal anggaran lolos dari `run()` | tidak — ditangani jadi status `budget` |

---

## 10. Crash access violation & kejujuran hasil pekerjaan (sikap bos: "yang gagal harus dikabarkan")

### 10.1 Crash -1073741819 (access violation) — akar ditemukan & diperbaiki

**Gejala** (laporan bos): Jarvis memantau & membalas email, lambat, lalu
crash tiba-tiba exit `-1073741819` setelah sesi Gemini hit-time-limit
(GoAway → 1008) dan reconnect. Konsol membanjiri
`QObject::setParent: Cannot set parent, new parent is in a different
thread` sebelum crash, dan stack berhenti di `ui.py` `mainloop`
(`QApplication.exec()`).

**Akar**: aplikasi menjalankan **Qt di main thread**, `JarvisLive.run()`
di **thread asyncio terpisah** (`main()` memanggil `asyncio.run` di
`threading.Thread`, lalu `ui.root.mainloop()`), dan callback sounddevice
di **thread ketiga**. Beberapa jalur UI menyentuh widget langsung dari
thread asyncio — paling sering `JarvisUI.set_services()`, yang dipanggil
**setiap reconnect** (`main.py` run loop) dan setiap jawaban otak.
Menyentuh widget dari dua thread merusak state internal Qt; yang terlihat
adalah warning `setParent` berulang dan akhirnya access violation di
dalam `app.exec()` — crash native tanpa traceback Python.

**Perbaikan**: semua jalur lintas-thread sekarang lewat sinyal Qt
(koneksi antar-thread otomatis *queued*, dijalankan di thread GUI):

| Facade (dipanggil dari thread asyncio/audio) | Dulu (langsung, membahayakan) | Sekarang |
|---|---|---|
| `set_services()` (tiap reconnect!) | `self._win.set_services(…)` | `_services_sig` → `_apply_services` |
| `refresh_model_ui()` | `_refresh_model_picker/_refresh_models_card` | `_refresh_ui_sig` → `_apply_refresh_ui` |
| `notify_phone_connected()` | `self._win.notify_phone_connected()` | `_phone_sig` → slot |
| `muted` (setter) | `_toggle_mute()` | `_mute_sig` → `_apply_mute` |

`hud.set_audio_level()` dan `hud.push_visemes()` **sengaja tetap** memanggil
metode HudCanvas langsung: keduanya hanya menulis atribut Python biasa
(`_live_amp`, `_visemes`) yang dibaca `_step()` di thread Qt — aman di
bawah GIL, dan itu tertulis di docstring-nya. Yang berbahaya adalah
sentuhan widget sungguhan.

### 10.2 Kegagalan senyap: "sudah selesai" untuk email yang gagal

`composio_actions` melaporkan setiap kegagalan sebagai **kalimat
Indonesia**, bukan exception — jalur suara membutuhkan itu. Akibatnya job
berstatus `done` walau email tidak terkirim, dan `_on_job_done` mengumumkan
"…sudah selesai" — pengguna harus bertanya dulu apakah emailnya terkirim.
Itu keluhan bos, dan itu yang diperbaiki:

- `Job` sekarang membawa **nama tool** (`Job.tool`, dari descriptor).
- `main._job_verdict(job)` mengembalikan `('done' | 'failed' |
  'waiting', ringkasan)` — status job saja **tidak** dipercaya;
  hasil `composio_actions` diperiksa dengan
  `composio_actions.looks_not_done()` (prefix kalimat kepemilikan plugin,
  diperiksa di posisi 0 agar isi email yang memuat kata "gagal" tidak
  salah tuduh).
- Pengumuman memakai kata yang sesuai: **GAGAL — <alasan>** (dengan
  instruksi tegas ke mulut: jangan mengaku berhasil), **menunggu
  konfirmasi** (kalimat gate diucapkan sebagai pertanyaan ya/tidak), atau
  **sudah selesai**.
- Pengumuman **ditunda** selagi otak custom memegang giliran
  (`self._in_brain`) supaya tidak menyela di tengah turn, lalu di-flush
  di akhir turn; `_flush_pending_job_msgs()` diam (tidak membuang pesan)
  bila belum ada sesi.

### 10.3 Konfirmasi ganda untuk satu email — dihapus

`composio_actions` dulu masuk kategori `EXTERNAL` → layer izin aplikasi
meminta konfirmasi, **lalu** gate plugin meminta lagi. Sekarang layer izin
memperlakukannya sebagai "pengguna memintanya langsung" (`MUTATING`), dan
**satu-satunya gerbang** adalah gate plugin: kalimat yang menyebut TEPAT apa
yang akan dikirim + token argumen identik (sha1, TTL 3 menit) + jejak audit
sendiri (`actor="plugin"` di `memory/audit.jsonl`). Tool keluar lain
(`send_message`, `telegram_remote`, `cloud_agent`) tetap seperti dulu.

### 10.4 Bukti pengujian

`_refwork/sonde_uithread.py` (semua OK): pemanggilan widget langsung dari
thread non-Qt hilang dari facade; sinyal dideklarasikan/disambungkan/punya
slot; `_job_verdict` membedakan gagal/menunggu/selesai termasuk kasus
"subjek email mengandung kata gagal"; `looks_not_done` terhadap kalimat
asli plugin; `Job.tool` dari descriptor; `composio_actions` tidak lagi
dikonfirmasi dua kali sementara `send_message`/`shutdown_jarvis`/
`screen_agent` tetap minta izin. Regresi: `run_tests.py` → 22 sonde lolos.
