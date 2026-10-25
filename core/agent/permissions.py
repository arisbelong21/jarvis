"""
core/agent/permissions.py — satu tempat memutuskan boleh/tidaknya sebuah aksi.

Kebijakan yang diminta bos:
    "harus konfirmasi dulu untuk tugas yang penting, kecuali memang sudah
    saya izinkan tanpa konfirmasi."

Karena itu klasifikasinya TIGA, bukan dua:

    allow    — aman & tidak mengubah apa pun (baca file, cari web, status).
               Boleh jalan tanpa bertanya.
    confirm  — mengubah sesuatu, mengirim keluar, atau menghapus.
               HARUS dikonfirmasi, kecuali tool itu ada di auto_allow.
    deny     — dimatikan total (tool_permissions.deny).

Klasifikasi default ditentukan dari nama tool + daftar kategori. Pengguna bisa
menimpanya per tool lewat config:

    "tool_permissions": {
        "deny": ["screen_agent"],
        "auto_allow": ["docx_writer"],     # tulis file tanpa konfirmasi
        "confirm": ["web_search"]          # baca pun minta izin
    }

Aturan menang: deny > confirm > auto_allow > kategori bawaan. Dengan begitu
satu entri di "confirm" tidak bisa dibatalkan diam-diam oleh kategori.

Fase 4 Slice 3 — izin yang diingat (core/tool_trust.py, berkas sendiri di
memory/tool_trust.json) duduk di ATAS aturan itu: trust hanya menaikkan
confirm → allow, dan hanya kalau flag trust.remember menyala. deny tetap
tak tergoyahkan; tool destruktif tak bisa di-trust sama sekali.
"""
from __future__ import annotations

# Nama tool yang jelas-jelas hanya membaca.
READ_ONLY = {
    "web_search", "weather_report", "system_status", "model_status",
    "recall_memory", "screen_process", "screen_coach", "flight_finder",
    "youtube_video", "file_processor", "document_review", "rea_guide",
    "manage_monitor", "close_camera", "brain", "custom_brain",
}

# Mengubah keadaan di mesin ini — tetapi pengguna yang memintanya secara
# eksplisit ("buatkan dokumen", "bangun proyek"), jadi tidak perlu izin kedua.
MUTATING = {
    "file_controller", "dev_agent", "code_helper", "docx_writer",
    "excel_writer", "open_app", "computer_settings", "switch_model",
    "game_updater", "video_player", "boss_key", "chat_takeover",
    "home_automation", "pomodoro", "quiz", "receipt_ledger", "screen_share",
    # composio_actions: setiap aksi KELUAR (kirim/balas/hapus email,
    # hapus/ubah acara, run_tool mutating) sudah digerbangi plugin sendiri
    # dengan kalimat yang menyebut TEPAT apa yang akan terjadi + token
    # argumen identik (TTL 3 menit) + jejak audit. Layer ini sengaja
    # meloloskannya — meminta konfirmasi dua kali untuk satu email adalah
    # keluhan bos. Baca saja sebagai "pengguna memintanya langsung".
    "composio_actions",
    # Tool bawaan yang jelas diminta pengguna dan tidak merusak. Tanpa entri
    # di sini mereka jatuh ke 'unknown' dan meminta izin setiap kali — itu
    # yang terjadi pada save_memory/agent_run sebelum audit ini.
    "save_memory", "agent_run", "reload_plugins",
}

# Bertindak sebagai TANGAN pengguna: mengklik dan mengetik di layar mereka.
# Ini yang paling mudah salah sasaran, jadi selalu minta izin.
HANDS_ON = {"screen_agent", "computer_control", "desktop_control"}

# computer_control punya 19 aksi dan tidak semuanya sama beratnya. Yang di
# bawah ini TIDAK menyentuh apa pun: hanya melihat, menunggu, atau menggeser
# pandangan. Meminta izin untuk `wait` pernah memblokir otak di tengah jawaban
# (24 Okt: otak memanggil computer_control(wait) lalu berhenti dan menjawab
# dalam bahasa Inggris karena respons tool-nya berisi "KONFIRMASI DIPERLUKAN").
_CC_READ_ONLY = {"screenshot", "screen_find", "move", "scroll", "wait",
                 "focus_window"}

# Mengirim keluar dari mesin — efeknya tidak bisa ditarik kembali.
#
# composio_actions TIDAK di sini melainkan di MUTATING: plugin itu punya
# gerbang konfirmasi sendiri di dalam _gate() — untuk SETIAP aksi keluar
# ia menjawab dengan kalimat yang menyebut TEPAT apa yang akan terjadi,
# lalu hanya mengeksekusi kalau confirm="ya" datang dengan argumen yang
# identik (token sha1, kedaluwarsa 3 menit). Meminta konfirmasi dua kali
# untuk satu email adalah keluhan bos; gerbang plugin yang jadi satu-satunya
# gerbang, dan ia mencatat jejak audit sendiri (actor="plugin").
EXTERNAL = {
    "send_message", "telegram_remote", "cloud_agent",
    "calendar_assistant", "meeting_notetaker", "price_tracker",
}

# Menghapus / mematikan.
DESTRUCTIVE = {"shutdown_jarvis", "undo"}


def classify(tool: str) -> str:
    """Kategori kasar sebuah tool.

    Tool yang tidak ada di daftar mana pun dikembalikan sebagai 'unknown',
    bukan 'mutating' — karena 'mutating' sekarang berarti "pengguna memintanya
    langsung" (allow). Tool baru dari plugin pihak ketiga tidak boleh otomatis
    dianggap aman hanya karena namanya belum dikenal.
    """
    t = (tool or "").strip().lower()
    if t in DESTRUCTIVE:
        return "destructive"
    if t in EXTERNAL:
        return "external"
    if t in HANDS_ON:
        return "hands_on"
    if t in READ_ONLY:
        return "read"
    if t in MUTATING:
        return "mutating"
    return "unknown"


def _decide_base(tool: str, config: dict | None = None,
                 known: bool | None = None,
                 args: dict | None = None,
                 internal: bool = False) -> tuple[str, str]:
    """('allow'|'confirm'|'deny', alasan singkat) — aturan dasar.

    `known=True` berarti tool ini benar-benar ada di registri (action/plugin/
    MCP). Tanpa itu, tool yang tidak terdaftar di kategori mana pun dianggap
    'unknown' dan selalu minta izin — yang membuat tool biasa seperti
    browser_control dan composio_connector bertanya terus. Tool yang TIDAK
    dikenal sama sekali tetap minta izin.

    `internal=True` berarti panggilan datang dari OTAK CUSTOM (jalur teliti),
    bukan dari Gemini. Untuk tool "tangan" itu berarti pengguna sudah memilih
    otak lewat tawaran sebelumnya — meminta izin lagi hanya menggandakan
    pertanyaan yang sama. `deny` tetap menang.
    """
    cfg = (config or {}).get("tool_permissions") or {}
    deny = {str(x).strip().lower() for x in (cfg.get("deny") or [])}
    auto = {str(x).strip().lower() for x in (cfg.get("auto_allow") or [])}
    must = {str(x).strip().lower() for x in (cfg.get("confirm") or [])}
    t = (tool or "").strip().lower()

    if t in deny:
        return "deny", "dimatikan di tool_permissions.deny"
    if t in must:
        return "confirm", "diminta konfirmasi eksplisit di config"
    if t in auto:
        return "allow", "diizinkan tanpa konfirmasi di config"

    cat = classify(t)
    if cat == "read":
        return "allow", "hanya membaca"
    if cat == "mutating":
        return "allow", "pengguna memintanya langsung"
    if cat == "hands_on":
        # Lihat/menunggu bukan "mengambil alih" — jangan minta izin untuk itu.
        act = str((args or {}).get("action") or "").strip().lower()
        if t == "computer_control" and act in _CC_READ_ONLY:
            return "allow", "hanya melihat/menunggu layar"
        # Audit 25 Okt (K2): bypass untuk otak DIPERSEMPIT ke computer_control.
        # Dulu berlaku untuk semua tool "tangan", sehingga `desktop_control`
        # (yang MENJALANKAN KODE hasil model) dan `screen_agent` ikut berjalan
        # tanpa izin saat dipanggil otak. Keduanya kembali minta izin.
        if internal and t == "computer_control":
            return "allow", "dikerjakan otak custom (jalur teliti)"
        # Audit 25 Okt (K-4): desktop_control MENJALANKAN kode buatan model
        # (exec) yang punya Path + pyautogui di sandbox-nya — ia bisa membaca/
        # menulis file di mana pun dan mengendalikan keyboard/mouse, bukan
        # sekadar "mengklik". Alasan konfirmasi harus jujur supaya pengguna
        # tahu persis apa yang mereka setujui.
        if t == "desktop_control":
            return "confirm", ("menjalankan KODE buatan model yang dapat "
                               "membaca/menulis file dan mengendalikan "
                               "keyboard/mouse")
        return "confirm", "mengklik/mengetik di layar pengguna"
    if cat == "destructive":
        return "confirm", "menghapus/mematikan"
    if cat == "external":
        return "confirm", "mengirim keluar — tidak bisa ditarik"
    if cat == "unknown" and known:
        # Ada di registri, hanya belum masuk kategori: perlakukan seperti
        # 'mutating' (allow). Ini yang menghilangkan permintaan izin yang
        # mengganggu untuk tool biasa.
        return "allow", "tool terdaftar yang belum dikategorikan"
    if cat == "unknown":
        return "confirm", "tool baru yang belum dikenal"
    return "confirm", "mengubah keadaan"


def _trust_enabled() -> bool:
    """Flag trust.remember dibaca hidup; gagal baca = OFF (perilaku lama)."""
    try:
        from config import get_flags
        return bool(get_flags().get("trust.remember", True))
    except Exception:                                   # noqa: BLE001
        return False


def decide(tool: str, config: dict | None = None,
           known: bool | None = None,
           args: dict | None = None,
           internal: bool = False) -> tuple[str, str]:
    """('allow'|'confirm'|'deny', alasan singkat).

    Fase 4 Slice 3: izin yang DIINGAT menaikkan confirm → allow, dipanggil
    di titik paling akhir — setelah deny/confirm/auto_allow kategori
    bawaan semuanya sudah bicara, jadi tool_permissions.deny tidak pernah
    bisa dikalahkan trust. Tool destruktif tak bisa di-trust sama sekali
    (tool_trust menolaknya), dan grant dengan args_hash hanya berlaku untuk
    argumen yang sama persis. Flag trust.remember mematikan seluruh
    angkatan ini → perilaku lama persis.
    """
    decision, why = _decide_base(tool, config, known, args=args,
                                 internal=internal)
    if decision == "confirm" and _trust_enabled():
        try:
            from core import tool_trust
            if tool_trust.is_trusted(tool, args):
                return ("allow",
                        "izin dipercaya — sudah diizinkan sebelumnya "
                        "(tool_trust)")
        except Exception:                               # noqa: BLE001
            pass
    return decision, why


def summary_line(tool: str, config: dict | None = None) -> str:
    """Untuk log/diagnosa: 'docx_writer → confirm (mengubah keadaan)'."""
    d, why = decide(tool, config)
    return f"{tool} → {d} ({why})"
