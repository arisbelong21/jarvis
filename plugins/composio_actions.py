"""
Composio executor — the voice path into 1,600+ connected apps.

composio_connector draws the Connections panel: catalog, statuses, Connect
Links. This module is the other half — actually DOING things: read and send
Gmail, create and edit Google Calendar events, and (through discover +
run_tool) any other toolkit a connected account can reach.

Design notes:
- One REST seam (_urlopen), zero dependencies, same conventions as the
  connector: every failure arrives as an Indonesian sentence for the model
  to speak, never as a traceback.
- Tool slugs and argument names drift when Composio republishes a toolkit,
  so curated actions never hardcode argument names: the live input schema
  is fetched (cached) and friendly arguments are mapped onto whatever the
  current schema actually calls them (to → recipient_email). discover and
  run_tool are the escape hatch when a curated action is not enough.
- Config namespace "composio" is shared with composio_connector: one API
  key + user_id powers both.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

# Audit 26 Okt — verifikasi berbasis kontrak (core/contract.py). Import dijaga:
# bila core tak tersedia, plugin tetap jalan tanpa menempel kontrak.
try:
    from core import contract as _contract
except Exception:                                # noqa: BLE001  # pragma: no cover
    _contract = None

PLUGIN = {
    "name": "composio_actions",
    "description": (
        "Melakukan AKSI Nyata di app yang sudah tersambung lewat Composio: "
        "Gmail (cari email, baca isi email, kirim, balas, simpan draft, hapus) dan "
        "Google Calendar (lihat agenda, detail acara, buat, ubah, hapus, quick-add, "
        "cari acara). Gunakan saat pengguna meminta membaca/mengirim/membalas email "
        "atau mengelola jadwal/kalender. Untuk app lain (GitHub, Slack, Notion, "
        "Drive, ~1.600 lainnya) pakai action 'discover' untuk melihat tool yang "
        "tersedia, lalu 'run_tool' dengan slug dan arguments yang tepat. Kalau pengguna "
        "menyebut waktu relatif ('besok', 'jam 3 sore') dan kamu tidak tahu tanggal "
        "hari ini, jalankan tool GOOGLECALENDAR_GET_CURRENT_DATE_TIME lewat run_tool "
        "dulu. Menanyakan app apa yang sudah tersambung = tugas composio_connector, "
        "bukan tool ini. PENTING: aksi yang mengirim sesuatu ke luar (kirim/balas/"
        "hapus email, hapus/ubah acara) TIDAK dieksekusi pada panggilan pertama — "
        "plugin membalas dengan kalimat konfirmasi. Sampaikan kalimat itu ke "
        "pengguna apa adanya; hanya setelah pengguna menyetujui, panggil lagi "
        "action yang sama dengan parameter confirm='ya'. Jangan pernah menebak "
        "alamat email, nama, atau isi pesan yang tidak diucapkan pengguna — kalau "
        "ragu, tanyakan dulu. EMAIL dan KALENDER selalu lewat tool ini: jangan "
        "memakai send_message (itu untuk WhatsApp/Telegram) dan jangan membuka "
        "aplikasi lain untuk mengirim email."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["gmail_search", "gmail_read", "gmail_send", "gmail_reply",
                         "gmail_draft", "gmail_delete",
                         "calendar_list", "calendar_get", "calendar_create",
                         "calendar_update", "calendar_delete", "calendar_quick_add",
                         "calendar_find", "discover", "run_tool"],
                "description": ("gmail_search=cari email; gmail_read=baca isi satu email "
                                "(butuh message_id); gmail_send=kirim email; gmail_reply=balas "
                                "satu thread; gmail_draft=simpan draft; gmail_delete=hapus satu "
                                "email; calendar_list=daftar acara; calendar_get=detail acara; "
                                "calendar_create=buat acara; calendar_update=ubah acara; "
                                "calendar_delete=hapus acara; calendar_quick_add=buat acara dari "
                                "kalimat bebas; calendar_find=cari acara; discover=daftar tool "
                                "sebuah app; run_tool=jalankan tool Composio apa pun."),
            },
            "query": {"type": "STRING", "description": "Kata kunci pencarian. Gmail: sintaks pencarian Gmail (mis. 'from:budi', 'is:unread'). Kalender: kata pada judul acara. discover: kata kunci tool."},
            "max_results": {"type": "INTEGER", "description": "Batas jumlah hasil (Gmail & kalender). Default 10."},
            "message_id": {"type": "STRING", "description": "ID pesan Gmail, dari hasil gmail_search."},
            "thread_id": {"type": "STRING", "description": "ID thread Gmail untuk balasan, dari hasil gmail_search."},
            "to": {"type": "STRING", "description": "Alamat email tujuan; pisahkan dengan koma untuk beberapa."},
            "cc": {"type": "STRING", "description": "Alamat CC."},
            "bcc": {"type": "STRING", "description": "Alamat BCC."},
            "subject": {"type": "STRING", "description": "Subjek email."},
            "body": {"type": "STRING", "description": "Isi email (teks polos kecuali is_html true)."},
            "is_html": {"type": "BOOLEAN", "description": "True kalau body memakai HTML."},
            "attachment": {"type": "STRING", "description": "URL lampiran, mis. https://contoh.com/file.pdf."},
            "event_id": {"type": "STRING", "description": "ID acara kalender, dari calendar_list/find."},
            "calendar_id": {"type": "STRING", "description": "ID kalender; 'primary' = kalender utama."},
            "title": {"type": "STRING", "description": "Judul acara kalender."},
            "start": {"type": "STRING", "description": "Waktu mulai acara, ISO 8601 (mis. '2026-10-20T09:00:00+07:00')."},
            "end": {"type": "STRING", "description": "Waktu selesai acara, ISO 8601. Kalau kosong, dihitung 1 jam setelah mulai."},
            "description": {"type": "STRING", "description": "Keterangan acara kalender."},
            "location": {"type": "STRING", "description": "Lokasi acara."},
            "attendees": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Daftar email peserta acara."},
            "timezone": {"type": "STRING", "description": "Zona waktu acara, mis. 'Asia/Jakarta'."},
            "text": {"type": "STRING", "description": "Untuk calendar_quick_add: satu kalimat acara, mis. 'rapat dengan Budi besok jam 3 sore'."},
            "app": {"type": "STRING", "description": "Untuk discover: slug toolkit (mis. 'gmail', 'googlecalendar', 'github', 'slack', 'notion')."},
            "tool": {"type": "STRING", "description": "Untuk run_tool: slug tool Composio (mis. 'GMAIL_SEND_EMAIL') — lihat hasil discover."},
            "arguments": {"type": "OBJECT", "description": "Untuk run_tool: objek argumen sesuai input schema tool tersebut."},
            "confirm": {"type": "STRING", "description": "Untuk aksi kirim/hapus: isi 'ya' HANYA setelah pengguna menyetujui secara lisan. Panggilan pertama tanpa confirm hanya meminta konfirmasi, belum menjalankan apa pun."},
        },
        "required": ["action"],
    },
}

# Curated actions: action -> {slug, toolkit, map(friendly -> candidate real
# parameter names, best first), defaults}. Slugs verified against the public
# Composio toolkit catalog (Gmail 63 tools, Google Calendar 40+) on 2026-10-19;
# the candidate lists exist because argument names are only resolved against
# the live schema at runtime, never assumed.
_ACTION_SPECS = {
    "gmail_search": {
        "slug": "GMAIL_FETCH_EMAILS", "toolkit": "gmail",
        "map": {"query": ["query", "search_query", "q"],
                "max_results": ["max_results", "maxResults", "limit", "result_count"]},
        "defaults": {"max_results": 10},
    },
    "gmail_read": {
        "slug": "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID", "toolkit": "gmail",
        "map": {"message_id": ["message_id", "messageId", "id"]},
    },
    "gmail_send": {
        "slug": "GMAIL_SEND_EMAIL", "toolkit": "gmail",
        "map": {"to": ["recipient_email", "recipient", "to", "to_email"],
                "cc": ["cc"], "bcc": ["bcc"],
                "subject": ["subject", "email_subject"],
                "body": ["body", "message", "email_body"],
                "is_html": ["is_html", "html", "body_is_html"],
                "attachment": ["attachment", "attachment_url", "attachments"]},
    },
    "gmail_reply": {
        "slug": "GMAIL_REPLY_TO_THREAD", "toolkit": "gmail",
        "map": {"thread_id": ["thread_id", "threadId"],
                "body": ["body", "message", "reply_body"],
                "is_html": ["is_html", "html"]},
    },
    "gmail_draft": {
        "slug": "GMAIL_CREATE_EMAIL_DRAFT", "toolkit": "gmail",
        "map": {"to": ["recipient_email", "recipient", "to", "to_email"],
                "cc": ["cc"], "subject": ["subject"],
                "body": ["body", "message", "email_body"],
                "is_html": ["is_html", "html"]},
    },
    "gmail_delete": {
        "slug": "GMAIL_DELETE_MESSAGE", "toolkit": "gmail",
        "map": {"message_id": ["message_id", "messageId", "id"]},
    },
    "calendar_list": {
        "slug": "GOOGLECALENDAR_EVENTS_LIST", "toolkit": "googlecalendar",
        "map": {"calendar_id": ["calendar_id", "calendarId", "calendar"],
                "time_min": ["time_min", "timeMin", "start_time", "from"],
                "time_max": ["time_max", "timeMax", "end_time", "to"],
                "max_results": ["max_results", "maxResults", "limit"],
                "query": ["query", "q"]},
        "defaults": {"calendar_id": "primary", "max_results": 25},
    },
    "calendar_get": {
        "slug": "GOOGLECALENDAR_EVENTS_GET", "toolkit": "googlecalendar",
        "map": {"event_id": ["event_id", "eventId", "id"],
                "calendar_id": ["calendar_id", "calendarId", "calendar"]},
        "defaults": {"calendar_id": "primary"},
    },
    "calendar_create": {
        "slug": "GOOGLECALENDAR_CREATE_EVENT", "toolkit": "googlecalendar",
        "map": {"calendar_id": ["calendar_id", "calendarId", "calendar"],
                "title": ["summary", "title", "event_title", "name"],
                "start": ["start_datetime", "start", "event_start", "start_time"],
                "end": ["end_datetime", "end", "event_end", "end_time"],
                "description": ["description", "details", "event_description"],
                "location": ["location", "venue", "event_location"],
                "attendees": ["attendees", "attendee_emails", "attendees_emails"],
                "timezone": ["timezone", "time_zone", "event_timezone"]},
        "defaults": {"calendar_id": "primary"},
    },
    "calendar_update": {
        "slug": "GOOGLECALENDAR_UPDATE_EVENT", "toolkit": "googlecalendar",
        "map": {"event_id": ["event_id", "eventId", "id"],
                "calendar_id": ["calendar_id", "calendarId", "calendar"],
                "title": ["summary", "title", "event_title", "name"],
                "start": ["start_datetime", "start", "event_start", "start_time"],
                "end": ["end_datetime", "end", "event_end", "end_time"],
                "description": ["description", "details", "event_description"],
                "location": ["location", "venue", "event_location"],
                "attendees": ["attendees", "attendee_emails", "attendees_emails"],
                "timezone": ["timezone", "time_zone", "event_timezone"]},
        "defaults": {"calendar_id": "primary"},
    },
    "calendar_delete": {
        "slug": "GOOGLECALENDAR_DELETE_EVENT", "toolkit": "googlecalendar",
        "map": {"event_id": ["event_id", "eventId", "id"],
                "calendar_id": ["calendar_id", "calendarId", "calendar"]},
        "defaults": {"calendar_id": "primary"},
    },
    "calendar_quick_add": {
        "slug": "GOOGLECALENDAR_QUICK_ADD", "toolkit": "googlecalendar",
        "map": {"text": ["text", "quick_add", "quick_add_text"],
                "calendar_id": ["calendar_id", "calendarId", "calendar"]},
        "defaults": {"calendar_id": "primary"},
    },
    "calendar_find": {
        "slug": "GOOGLECALENDAR_FIND_EVENT", "toolkit": "googlecalendar",
        "map": {"query": ["query", "q", "search"],
                "calendar_id": ["calendar_id", "calendarId", "calendar"],
                "time_min": ["time_min", "start_time"],
                "time_max": ["time_max", "end_time"]},
        "defaults": {"calendar_id": "primary"},
    },
}

# Friendly parameters that MUST be present before the action may run.
_REQUIRE = {
    "gmail_read": ["message_id"],
    "gmail_send": ["to", "subject", "body"],
    "gmail_reply": ["thread_id", "body"],
    "gmail_draft": ["to", "subject", "body"],
    "gmail_delete": ["message_id"],
    "calendar_get": ["event_id"],
    "calendar_create": ["title", "start"],
    "calendar_update": ["event_id", "title", "start", "end", "description",
                        "location", "attendees"],
    "calendar_delete": ["event_id"],
    "calendar_quick_add": ["text"],
}

_TOOLKIT_LABELS = {"gmail": "Gmail", "googlecalendar": "Google Calendar"}

# ── the confirmation gate: nothing leaves the machine on a hallucination ────
#
# A speech model occasionally invents arguments — an address nobody said, a
# message body nobody dictated. One misheard sentence must never become a sent
# email, so every outward-facing action runs in two steps: the first call
# describes exactly what is about to happen and stops; only a second call with
# confirm="ya" on the SAME delivery target executes. The pending record lives
# three minutes — a stale "ya" can never fire.
#
# 22 Okt — sidik jari konfirmasi TIDAK meng-hash teks bebas (body/pesan):
# audit 05:28-05:29 menunjukkan 8 permintaan konfirmasi dalam 70 detik karena
# model menulis ulang body tiap giliran, sehingga hash argumen tak pernah
# cocok. Sekarang yang di-hash hanya bagian yang berisiko (penerima, subjek,
# lampiran, id), dan yang DIEKSEKUSI adalah payload tersimpan — jadi
# "yang disetujui = yang dikirim", bukan hasil regenerasi model.
_FREE_TEXT_KEYS = {"body", "text", "message", "content", "note",
                   "description", "html", "plain", "confirm"}

_GATED_ACTIONS = {"gmail_send", "gmail_reply", "gmail_delete",
                  "calendar_delete", "calendar_update"}
_MUTATING_SLUG = re.compile(
    r"(SEND|REPLY|DELETE|REMOVE|UPDATE|TRASH|PUBLISH|ARCHIVE|MARK_|MOVE_)")

_EMAIL_RE = re.compile(r"^[^@\s,]+@[^@\s,]+\.[A-Za-z]{2,}$")
_RESERVED_DOMAINS = ("example.com", "example.org", "example.net",
                     "test.com", "localhost", "email.com")

_pending: dict[str, tuple] = {}     # token -> (expires_at, args snapshot)
_PENDING_TTL = 180.0
_pending_lock = threading.Lock()


def _split_emails(raw) -> list[str]:
    """One address or a comma/semicolon list, into clean pieces."""
    if isinstance(raw, list):
        parts: list[str] = []
        for x in raw:
            parts.extend(str(x).replace(";", ",").split(","))
    else:
        parts = str(raw or "").replace(";", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def _bad_email(action: str, params: dict, args: dict) -> str:
    """The first address that must never be sent to, or "" when all are fine.

    Guards both shapes: the friendly parameters and the already-mapped
    arguments (recipient_email and friends), because a hallucination can sit
    in either."""
    if action in ("gmail_send", "gmail_draft"):
        fields = ("to", "cc", "bcc")
    elif action in ("calendar_create", "calendar_update"):
        fields = ("attendees",)
    else:
        return ""
    for f in fields:
        for addr in _split_emails(params.get(f) if params.get(f) is not None
                                  else args.get(f)):
            low = addr.lower()
            if not _EMAIL_RE.match(addr):
                return f"“{addr}” bukan alamat email yang sah"
            dom = low.rsplit("@", 1)[-1]
            if any(dom == d or dom.endswith("." + d)
                   for d in _RESERVED_DOMAINS):
                return (f"“{addr}” memakai domain contoh yang disimpan untuk "
                        "dokumentasi — email tidak dikirim ke sana")
    return ""


# ── GrokBot 22 Okt: cek penerima terhadap riwayat email TERKIRIM ────────────
# Transkrip suara sering salah dengar satu huruf ("budi@" untuk
# "budhi@"). Sebelum gerbang konfirmasi, setiap alamat dicocokkan
# dengan alamat yang pernah dikirimi: dari cache lokal memory/
# sent_recipients.json (diisi setiap kali email sukses terkirim) dan dari
# folder Terkirim Gmail (GMAIL_FETCH_EMAILS query "in:sent", sekali per 10
# menit). TIDAK pernah dikoreksi diam-diam: alamat baru atau yang mirip
# alamat lain disebut di kalimat konfirmasi, jadi pengguna yang memutuskan.

_RECIP_PATH = Path(__file__).resolve().parent.parent / "memory" / "sent_recipients.json"
_RECIP_TTL = 600.0
_recip_cache: dict = {"ts": 0.0, "addrs": set()}
_ADDR_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _local_recipients() -> set:
    try:
        raw = json.loads(_RECIP_PATH.read_text(encoding="utf-8"))
        return {str(a).lower() for a in (raw.get("addresses") or [])}
    except Exception:
        return set()


def _remember_recipients(params: dict) -> None:
    addrs = set()
    for f in ("to", "cc", "bcc"):
        addrs |= {a.lower() for a in _split_emails(params.get(f))
                  if _EMAIL_RE.match(a)}
    if not addrs:
        return
    try:
        known = _local_recipients() | addrs
        _RECIP_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _RECIP_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"addresses": sorted(known)}, indent=1),
                       encoding="utf-8")
        tmp.replace(_RECIP_PATH)
        _recip_cache["addrs"] = set(_recip_cache["addrs"]) | addrs
    except Exception as e:                               # noqa: BLE001
        print(f"[Composio] riwayat penerima tidak tersimpan: {e}")


def _sent_recipients(key: str, user: str) -> set:
    """Alamat yang pernah dikirimi (lokal + folder Terkirim Gmail)."""
    addrs = set(_local_recipients())
    now = time.time()
    if now - _recip_cache["ts"] < _RECIP_TTL:
        return addrs | set(_recip_cache["addrs"])
    remote: set = set()
    try:
        spec = _ACTION_SPECS["gmail_search"]
        friendly = {"query": "in:sent", "max_results": 50}
        args, _ = _resolve(friendly, spec["map"], _tool_schema(key, spec["slug"]))
        body = _execute_tool(key, spec["slug"], user, args)
        blob = json.dumps(_unwrap(body, spec["slug"]), ensure_ascii=False, default=str)
        # Hanya kolom PENERIMA — pengirim (alamat bos sendiri) tidak dihitung.
        for m in re.finditer(r'"(?:to|recipient[s]?|cc|bcc)"\s*:\s*"([^"]+)"', blob, re.I):
            remote |= {a.lower() for a in _ADDR_IN_TEXT.findall(m.group(1))}
        _recip_cache["ts"] = now
        _recip_cache["addrs"] = remote
    except Exception as e:                               # noqa: BLE001
        print(f"[Composio] riwayat terkirim tidak terbaca: {e}")
    return addrs | remote


def recipient_note(params: dict, known: set) -> str:
    """Kalimat peringatan untuk gerbang konfirmasi, '' bila semua dikenal.

    Murni (tanpa jaringan) supaya bisa diuji. Tidak mengubah alamat apa pun."""
    import difflib
    notes = []
    for f in ("to", "cc", "bcc"):
        for addr in _split_emails(params.get(f)):
            low = addr.lower()
            if not known or low in known:
                continue
            close = difflib.get_close_matches(low, sorted(known), n=1, cutoff=0.82)
            if close:
                notes.append(f"PERHATIAN: “{addr}” belum pernah Anda kirimi, "
                             f"tapi mirip “{close[0]}” di riwayat terkirim — "
                             f"tanyakan mana yang benar")
            else:
                notes.append(f"catatan: “{addr}” alamat baru (belum ada di "
                             f"riwayat terkirim) — pastikan ejaannya")
    return "; ".join(notes)


def name_suggestions(raw: str, known: set) -> list:
    """Alamat di riwayat yang memuat nama yang diucapkan (bukan alamat)."""
    k = re.sub(r"[^a-z0-9]", "", str(raw or "").lower())
    if len(k) < 3:
        return []
    return sorted(a for a in known
                  if k in re.sub(r"[^a-z0-9]", "", a.split("@")[0]))[:3]


def _gate_sentence(action: str, params: dict, args: dict) -> str:
    """What the user is about to approve, in Indonesian. "" = no gate."""
    if action == "gmail_send":
        to = params.get("to") or args.get("recipient_email") or "?"
        subj = str(params.get("subject") or args.get("subject")
                   or "(tanpa subjek)")
        note = str(params.get("_recipient_note") or "")
        return (f"kirim email ke {to} dengan subjek “{subj}”"
                + (f" ({note})" if note else ""))
    if action == "gmail_reply":
        return f"balas thread Gmail {params.get('thread_id') or '?'}"
    if action == "gmail_delete":
        return f"hapus email {params.get('message_id') or '?'}"
    if action == "calendar_delete":
        return (f"hapus acara “{params.get('title') or 'acara'}” "
                "dari kalender")
    if action == "calendar_update":
        return (f"ubah acara “{params.get('title') or 'acara'}” "
                "di kalender")
    return ""


def _gate_token(action: str, args: dict) -> str:
    """Sidik jari konfirmasi: hanya bagian yang berisiko, bukan teks bebas.

    Body/pesan dikecualikan karena model menulis ulang kalimatnya tiap
    giliran; penerima, subjek, lampiran, dan id tetap ikut supaya "alamat
    yang ditukar" tidak bisa menumpang izin lama.
    """
    core = {k: v for k, v in (args or {}).items()
            if str(k).lower() not in _FREE_TEXT_KEYS}
    blob = json.dumps({"a": action, "args": core}, sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _audit(action: str, args: dict, decision: str, summary: str) -> None:
    """Jejak audit gerbang ini.

    Dulu jejak konfirmasi ditulis oleh layer izin aplikasi — tapi
    composio_actions sengaja dikeluarkan dari kategori EXTERNAL supaya satu
    email tidak diminta konfirmasi DUA kali. Pihak yang sekarang memegang
    gerbang adalah plugin ini, jadi plugin ini juga yang mencatat."""
    try:
        from core.agent import audit as _audit_mod
        _audit_mod.record(
            "composio_actions",
            {"action": action,
             **{k: v for k, v in (args or {}).items() if k != "confirm"}},
            actor="plugin", decision=decision,
            ok=(decision == "allow"), summary=summary)
    except Exception:
        pass


def _gate(action: str, params: dict, slug: str, args: dict):
    """(may_execute, message). may_execute False → message is the sentence
    the model must speak to the user; nothing has been executed."""
    with _pending_lock:
        now = time.time()
        for tok in [t for t, (exp, _) in _pending.items() if exp <= now]:
            _pending.pop(tok, None)

    gated = action in _GATED_ACTIONS or (
        action == "run_tool" and bool(_MUTATING_SLUG.search(slug or "")))
    if not gated:
        return True, ""

    sentence = _gate_sentence(action, params, args) or (
        f"jalankan {slug} dengan argumen {args}")
    token = _gate_token(action, args)

    if str(params.get("confirm") or "").strip().lower() in ("ya", "yes", "y"):
        with _pending_lock:
            hit = _pending.pop(token, None)
        if hit is not None:
            if hit[0] > time.time():
                # Yang disetujui = yang dikirim: pulihkan payload tersimpan
                # supaya teks hasil regenerasi model tidak ikut terkirim.
                stored = hit[1] or {}
                if isinstance(args, dict) and stored:
                    args.clear()
                    args.update(copy.deepcopy(stored))
                _audit(action, args, "allow",
                       f"disetujui pengguna — {sentence}")
                return True, ""                  # approved, same target
            _audit(action, args, "confirm",
                   "konfirmasi kedaluwarsa, minta ulang")
            return False, ("Konfirmasi itu sudah kedaluwarsa. Ulangi "
                           "perintahnya — saya konfirmasi ulang sebelum "
                           "menjalankan.")
        # No pending record for THESE arguments: a "ya" attached to different
        # content is a new request, not an approval. Fall through and ask
        # again — never let a swapped address ride on an old yes.

    with _pending_lock:
        _pending[token] = (time.time() + _PENDING_TTL, copy.deepcopy(args))
    _audit(action, args, "confirm", f"menunggu izin — {sentence}")
    return False, (f"KONFIRMASI DIPERLUKAN — {sentence}. Sampaikan kalimat ini "
                   "ke pengguna dan minta jawaban ya/tidak; panggil lagi "
                   "action yang sama dengan confirm='ya' hanya setelah "
                   "pengguna setuju.")

# Indonesian -> toolkit slug, for the discover action.
_APP_ALIASES = {
    "gmail": "gmail", "email": "gmail", "surat": "gmail", "gmail.com": "gmail",
    "googlecalendar": "googlecalendar", "kalender": "googlecalendar",
    "google calendar": "googlecalendar", "calendar": "googlecalendar",
    "google kalender": "googlecalendar", "agenda": "googlecalendar",
    "github": "github", "git": "github",
    "slack": "slack", "notion": "notion", "googledrive": "googledrive",
    "drive": "googledrive", "googlesheets": "googlesheets", "sheets": "googlesheets",
    "google sheets": "googlesheets", "spreadsheet": "googlesheets",
    "discord": "discord", "telegram": "telegram", "whatsapp": "whatsapp",
    "linear": "linear", "asana": "asana", "trello": "trello", "jira": "jira",
    "confluence": "confluence", "figma": "figma", "airtable": "airtable",
    "hubspot": "hubspot", "salesforce": "salesforce", "spotify": "spotify",
    "youtube": "youtube", "twitter": "twitter", "linkedin": "linkedin",
}

# ── configuration & caches ──────────────────────────────────────────────────

_BASE = "https://backend.composio.dev"
_API_VERSIONS = ("/api/v3.1", "/api/v3")   # v3.1 is current; v3 kept as fallback
_TIMEOUT = 45
_SCHEMA_TTL = 600.0        # tool schemas drift rarely — 10 minutes is plenty
_CONN_TTL = 60.0           # connection status must stay fresh for pre-checks
_DISCOVER_CAP = 40
_MAX_TEXT = 3000           # voice sessions: a wall of JSON is worthless


class ComposioError(RuntimeError):
    """A failure carrying a message written for the person hearing the answer."""


class ComposioHTTPError(ComposioError):
    """An HTTP-level rejection, with the status kept for tailored hints."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _urlopen(req, timeout):
    """The single seam every HTTP call goes through — the sonde replaces this."""
    return urllib.request.urlopen(req, timeout=timeout)


def _config() -> dict:
    """The shared 'composio' namespace: one API key + user_id for both plugins."""
    try:
        from memory.config_manager import get_plugin_config
        return get_plugin_config("composio") or {}
    except Exception:
        return {}


def _err_text(err) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err.get("details") or err.get("code") or err)
    return str(err)


def _http_error_message(code: int, raw: str) -> str:
    try:
        data = json.loads(raw)
        msg = (data.get("error") or {}).get("message") or ""
    except Exception:
        msg = raw[:200]
    if code == 401:
        return ("API key ditolak Composio (401). Periksa kunci di kartu "
                "Composio — salin ulang dari dashboard.composio.dev.")
    if code == 429:
        return "Composio membatasi laju permintaan (429). Tunggu sebentar."
    if code == 404:
        return "Composio: resource tidak ditemukan (HTTP 404)."
    return f"Composio menolak permintaan (HTTP {code}): {msg or 'tanpa keterangan'}"


def _api(key: str, method: str, path: str, params: dict | None = None,
         body: dict | None = None):
    """One REST call against Composio. Returns parsed JSON; raises
    ComposioHTTPError / ComposioError with a human message."""
    last = ""
    for prefix in _API_VERSIONS:
        url = _BASE + prefix + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("x-api-key", key)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with _urlopen(req, _TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            msg = _http_error_message(e.code, raw)
            if e.code == 404 and prefix != _API_VERSIONS[-1]:
                last = msg
                continue                    # older deployment: try the other version
            raise ComposioHTTPError(msg, e.code)
        except urllib.error.URLError as e:
            raise ComposioError(
                f"Tidak bisa menghubungi Composio ({getattr(e, 'reason', e)}). "
                "Cek koneksi internet.")
        except Exception as e:
            raise ComposioError(f"Composio: {e}")
        try:
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            raise ComposioError("Composio mengirim balasan yang tidak bisa dibaca.")
    raise ComposioError(last or "Composio menolak permintaan.")


def _execute_tool(key: str, slug: str, user_id: str, arguments: dict) -> dict:
    """POST /tools/execute/{slug} — the one call that makes things happen."""
    return _api(key, "POST", "/tools/execute/" + urllib.parse.quote(slug),
                body={"user_id": user_id, "arguments": arguments or {}})


_schema_lock = threading.Lock()
_schema_cache: dict[str, tuple[float, dict, str]] = {}
_conn_lock = threading.Lock()
_conn_cache: dict = {"ts": 0.0, "key": "", "user": "", "slugs": set(), "ok": False}


def _flat_params(raw) -> dict:
    """Normalise input_parameters into {name: spec}.

    Composio now publishes it as a JSON Schema object ({"type", "title",
    "properties", "required"}) and used to publish a flat {name: spec} map.
    Both shapes are accepted here, because reading the new one as the old one
    finds zero parameters — which made every friendly argument look
    "unmatched" and broke gmail_send / gmail_read / gmail_search while the
    tools themselves were perfectly fine.
    """
    raw = raw if isinstance(raw, dict) else {}
    if isinstance(raw.get("properties"), dict):
        required = set(raw.get("required") or [])
        out: dict = {}
        for name, spec in raw["properties"].items():
            spec = dict(spec) if isinstance(spec, dict) else {}
            if name in required:
                spec["required"] = True
            out[str(name)] = spec
        return out
    return {str(k): (v if isinstance(v, dict) else {})
            for k, v in raw.items()}


def _tool_schema(key: str, slug: str) -> dict | None:
    """Live input_parameters for a tool slug, or None when unreachable.
    This is what keeps curated actions honest: argument names come from the
    catalog, not from this file's assumptions."""
    with _schema_lock:
        hit = _schema_cache.get(slug)
        if hit and hit[2] == key and (time.time() - hit[0]) < _SCHEMA_TTL:
            return hit[1]
    try:
        data = _api(key, "GET", "/tools/" + urllib.parse.quote(slug),
                    params={"toolkit_versions": "latest"})
    except ComposioError:
        return None
    params = _flat_params(data.get("input_parameters"))
    with _schema_lock:
        _schema_cache[slug] = (time.time(), params, key)
    return params


def _active_toolkits(key: str, user_id: str) -> set[str] | None:
    """Toolkit slugs with at least one ACTIVE connected account, or None when
    the check itself failed (caller then proceeds and lets the real error
    surface). Fail-open on transport errors, never on a confident answer."""
    with _conn_lock:
        c = _conn_cache
        if c["ok"] and c["key"] == key and c["user"] == user_id \
                and (time.time() - c["ts"]) < _CONN_TTL:
            return c["slugs"]
    try:
        params = {"limit": 200}
        if user_id:
            params["user_ids"] = user_id
        data = _api(key, "GET", "/connected_accounts", params=params)
    except ComposioError:
        return None
    slugs: set[str] = set()
    for it in (data.get("items") or []):
        if not isinstance(it, dict):
            continue
        if str(it.get("status") or "").upper() != "ACTIVE":
            continue
        tk = it.get("toolkit") or {}
        slug = str(tk.get("slug") or "")
        if slug:
            slugs.add(slug)
    with _conn_lock:
        _conn_cache.update(ts=time.time(), key=key, user=user_id,
                           slugs=slugs, ok=True)
    return slugs


# ── friendly -> tool argument resolution ────────────────────────────────────

def _blank(v) -> bool:
    if v is None or v is False:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and not v:
        return True
    return False


def _plus_hour(iso: str) -> str:
    """start + 1 hour for events whose end the speaker did not name."""
    s = str(iso or "").strip().replace("Z", "+00:00")
    try:
        from datetime import datetime, timedelta
        return (datetime.fromisoformat(s) + timedelta(hours=1)).isoformat()
    except Exception:
        return ""


def _resolve(friendly: dict, arg_map: dict, schema: dict | None):
    """Map friendly names onto the tool's real parameter names.
    schema=None (unreachable) falls back to the primary candidate; a live
    schema picks the first candidate the tool actually declares."""
    out, unmatched = {}, []
    for fname, fval in friendly.items():
        if _blank(fval):
            continue
        candidates = arg_map.get(fname) or [fname]
        chosen = candidates[0] if schema is None else ""
        if schema is not None:
            for cand in candidates:
                if cand in schema:
                    chosen = cand
                    break
        if chosen:
            out[chosen] = fval
        else:
            unmatched.append(fname)
    return out, unmatched


def _friendly_args(action: str, params: dict, spec: dict) -> dict:
    """The friendly values for this action, with defaults and small fixes."""
    friendly: dict = {}
    for fname in spec["map"]:
        friendly[fname] = params.get(fname)
    for key, default in (spec.get("defaults") or {}).items():
        if _blank(friendly.get(key)):
            friendly[key] = default
    if action == "calendar_create" and not _blank(friendly.get("start")) \
            and _blank(friendly.get("end")):
        end = _plus_hour(str(friendly["start"]))
        if end:
            friendly["end"] = end
    if not _blank(friendly.get("attendees")) and isinstance(friendly["attendees"], str):
        friendly["attendees"] = [a.strip() for a in friendly["attendees"].split(",")
                                 if a.strip()]
    mr = friendly.get("max_results")
    if not _blank(mr):
        try:
            friendly["max_results"] = max(1, min(100, int(mr)))
        except (TypeError, ValueError):
            friendly["max_results"] = 10
    return friendly


def _toolkit_from_slug(slug: str) -> str:
    head = slug.split("_", 1)[0].lower() if slug else ""
    low = slug.lower()
    if head.startswith("google"):
        for cand in ("googlecalendar", "googledrive", "googlesheets",
                     "googlemeet", "googlecontacts", "googletasks"):
            if low.startswith(cand):
                return cand
    return head


def _unwrap(body, slug: str = ""):
    """Dig the tool's payload out of the execute envelope.
    Envelope shapes vary across toolkits: the payload may sit directly in
    data, or nested under data.response_data / data.data. Failure is signalled
    either by an error field or successful:false — both are translated."""
    node, depth = body, 0
    while isinstance(node, dict) and depth < 4:
        err = node.get("error")
        if err:
            raise ComposioError(f"Tool {slug} gagal: {_err_text(err)}" if slug
                                else _err_text(err))
        if node.get("successful") is False:
            why = _err_text(node.get("error") or node.get("message")
                            or "Composio menjalankan tool tapi melaporkan kegagalan "
                               "tanpa keterangan.")
            raise ComposioError(f"Tool {slug} gagal: {why}" if slug else why)
        nxt = None
        for k in ("response_data", "data"):
            v = node.get(k)
            if isinstance(v, (dict, list)):
                nxt = v
                break
        if nxt is None:
            break
        node, depth = nxt, depth + 1
    return node


# ── rendering for the voice session ─────────────────────────────────────────

def _norm(k: str) -> str:
    return str(k).lower().replace("_", "")


def _pick(d, *names) -> object:
    """Case/underscore-insensitive field lookup: messageId == message_id."""
    if not isinstance(d, dict):
        return ""
    want = {_norm(n) for n in names}
    for k, v in d.items():
        if _norm(k) in want and not _blank(v):
            return v
    return ""


def _squash(text, limit: int = 500) -> str:
    return " ".join(str(text).split())[:limit]


def _fmt_attendees(v) -> str:
    if isinstance(v, list):
        out = []
        for a in v:
            if isinstance(a, dict):
                out.append(str(_pick(a, "email", "displayName", "name") or "?"))
            else:
                out.append(str(a))
        return ", ".join(out)
    return str(v)


def _message_line(m: dict, n: int) -> str:
    who = _squash(_pick(m, "from", "sender", "fromemail", "from_"), 80)
    subj = _squash(_pick(m, "subject", "title"), 120) or "(tanpa subjek)"
    date = _squash(_pick(m, "date", "receivedatetime", "internaldate"), 40)
    mid = _squash(_pick(m, "messageid", "message_id", "id"), 60)
    tid = _squash(_pick(m, "threadid", "thread_id"), 60)
    body = _pick(m, "body", "textbody", "bodytext", "snippet", "message")
    line = f"{n}. “{subj}”"
    if who:
        line += f" — {who}"
    if date:
        line += f" ({date})"
    lines = [line]
    if mid or tid:
        lines.append(f"   [message_id: {mid or '-'} · thread_id: {tid or '-'}]")
    if body:
        lines.append("   " + _squash(body, 500))
    return "\n".join(lines)


def _event_line(e: dict, n: int) -> str:
    title = _squash(_pick(e, "summary", "title"), 120) or "(tanpa judul)"
    start = _squash(_pick(e, "start", "startdatetime", "starttime", "start_time"), 40)
    end = _squash(_pick(e, "end", "enddatetime", "endtime", "end_time"), 40)
    loc = _squash(_pick(e, "location"), 80)
    eid = _squash(_pick(e, "eventid", "event_id", "id"), 60)
    att = _pick(e, "attendees", "attendeeemails")
    line = f"{n}. {title}"
    if start and end:
        line += f" — {start} → {end}"
    elif start or end:
        line += f" — {start or end}"
    if loc:
        line += f" @ {loc}"
    lines = [line]
    ids = ""
    if eid:
        ids = f"   [event_id: {eid}]"
    if not _blank(att):
        ids += f" peserta: {_fmt_attendees(att)}"
    if ids:
        lines.append(ids)
    return "\n".join(lines)


def _render_item(item, n: int) -> str:
    if isinstance(item, dict):
        if _pick(item, "subject", "from", "sender", "messageid"):
            return _message_line(item, n)
        if _pick(item, "summary", "title", "start", "startdatetime"):
            return _event_line(item, n)
        return f"{n}. " + _squash(json.dumps(item, ensure_ascii=False), 300)
    return f"{n}. " + _squash(str(item), 300)


def _find_list(d: dict):
    for key in ("messages", "emails", "events", "items", "results",
                "value", "data", "drafts", "calendars"):
        v = d.get(key)
        if isinstance(v, list):
            return key, v
    for v in d.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return "", v
    return None


def _render_dict(d: dict) -> str:
    lines = []
    for k, v in list(d.items())[:20]:
        if _blank(v):
            continue
        lines.append(f"{k}: {_squash(v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False), 200)}")
    return "\n".join(lines) or "(tidak ada isi)"


def _render_payload(value) -> str:
    if _blank(value) and not isinstance(value, (int, float)):
        return "(tanpa hasil)"
    if isinstance(value, str):
        text = value.strip() or "(kosong)"
    elif isinstance(value, list):
        items = value[:50]
        text = "\n".join(_render_item(i, n + 1) for n, i in enumerate(items)) \
            or "(daftar kosong)"
    elif isinstance(value, dict):
        if _pick(value, "subject", "from", "sender", "messageid"):
            text = _message_line(value, 1)
        else:
            found = _find_list(value)
            if found and found[1]:
                _, items = found
                text = f"{len(items)} hasil:\n" + "\n".join(
                    _render_item(i, n + 1) for n, i in enumerate(items[:50]))
            else:
                text = _render_dict(value)
    else:
        text = str(value)
    if len(text) > _MAX_TEXT:
        text = (text[:_MAX_TEXT].rstrip()
                + f"\n[hasil dipotong pada {_MAX_TEXT} karakter — minta jumlah "
                  "lebih sedikit atau lebih spesifik]")
    return text


# ── success lines ───────────────────────────────────────────────────────────

def _success_line(action: str, params: dict, payload) -> str:
    if action == "gmail_send":
        to = str(params.get("to") or "tujuan")
        subj = str(params.get("subject") or "(tanpa subjek)")
        extra = ""
        mid = _squash(_pick(payload, "messageid", "message_id", "id"), 60)
        if mid:
            extra = f" (message_id: {mid})"
        return f"Email terkirim ke {to} — subjek “{subj}”{extra}."
    if action == "gmail_reply":
        return f"Balasan terkirim di thread {params.get('thread_id')}."
    if action == "gmail_draft":
        return f"Draft tersimpan — subjek “{params.get('subject') or '(tanpa subjek)'}”."
    if action == "gmail_delete":
        return f"Email {params.get('message_id')} dihapus."
    if action == "calendar_create":
        title = str(params.get("title") or "(tanpa judul)")
        when = str(params.get("start") or "")
        if params.get("end"):
            when += f" → {params.get('end')}"
        eid = _squash(_pick(payload, "eventid", "event_id", "id"), 60)
        extra = f" (event_id: {eid})" if eid else ""
        return f"Acara “{title}” dibuat{' — ' + when if when else ''}{extra}."
    if action == "calendar_update":
        return f"Acara {params.get('event_id')} diperbarui."
    if action == "calendar_delete":
        return f"Acara {params.get('event_id')} dihapus."
    if action == "calendar_quick_add":
        return f"Acara dibuat dari “{params.get('text')}”."
    return ""


# ── discovery ───────────────────────────────────────────────────────────────

def _normalize_app(raw: str) -> str:
    app = str(raw or "").strip().lower()
    if not app:
        return ""
    if app in _APP_ALIASES:
        return _APP_ALIASES[app]
    squashed = app.replace(" ", "").replace("-", "")
    return _APP_ALIASES.get(squashed, squashed)


def _discover(key: str, params: dict) -> str:
    app = _normalize_app(params.get("app"))
    if not app:
        return ("Sebutkan app-nya (mis. 'gmail', 'googlecalendar', 'github', "
                "'slack', 'notion') untuk melihat tool yang tersedia.")
    query = str(params.get("query") or "").strip()
    api_params = {"toolkit_slug": app, "limit": 100, "toolkit_versions": "latest"}
    if query:
        api_params["query"] = query
    data = _api(key, "GET", "/tools", params=api_params)
    items = [t for t in (data.get("items") or []) if isinstance(t, dict)
             and not t.get("is_deprecated")]
    if not items:
        return (f"Tidak ada tool yang cocok di toolkit '{app}'"
                + (f" untuk kata kunci “{query}”" if query else "")
                + ". Coba app lain atau kata kunci lebih pendek.")
    lines = [f"{app}: {len(items)} tool tersedia "
             f"(menampilkan maksimal {min(len(items), _DISCOVER_CAP)}):"]
    for t in items[:_DISCOVER_CAP]:
        slug = str(t.get("slug") or "")
        desc = _squash(t.get("description") or t.get("name") or "", 110)
        line = f"• {slug}" + (f" — {desc}" if desc else "")
        ipp = _flat_params(t.get("input_parameters"))
        if ipp:
            req = [k for k, v in ipp.items()
                   if isinstance(v, dict) and v.get("required")]
            opt = [k for k, v in ipp.items()
                   if isinstance(v, dict) and not v.get("required")]
            if req:
                line += f"\n    wajib: {', '.join(req)}"
            if opt:
                line += f"\n    opsional: {', '.join(opt[:8])}"
        lines.append(line)
    lines.append("Lalu gunakan action 'run_tool': 'tool' = slug di atas, "
                 "'arguments' = objek sesuai nama parameter tersebut.")
    text = "\n".join(lines)
    if len(text) > _MAX_TEXT:
        text = (text[:_MAX_TEXT].rstrip()
                + f"\n[daftar dipotong — pakai 'query' untuk menyaring tool]")
    return text


# ── dispatch ────────────────────────────────────────────────────────────────

def _detail_line(payload) -> str:
    """One compact line of the payload's interesting leftovers for actions
    that already speak a success sentence (status, threadId, times, link…).
    Identifiers are left out: the success line already carries them."""
    if not isinstance(payload, dict):
        return ""
    skip = {"messageid", "id", "eventid"}
    parts = []
    for k, v in payload.items():
        if _norm(k) in skip or _blank(v):
            continue
        if _norm(k) in ("threadid", "status", "summary", "start", "end",
                        "htmllink", "joinurl", "conferenceurl", "location"):
            parts.append(f"{k}: {_squash(v, 80)}")
        if len(parts) >= 4:
            break
    return " · ".join(parts)


def _attach_contract(action: str, text: str, payload) -> str:
    """Tempel kontrak verifikasi untuk aksi keluar yang punya bukti delivery.

    Hanya gmail_send / gmail_reply / calendar_create — aksi yang dulu dinilai
    verify() lewat cocok-teks ("Email terkirim", "(message_id: …)"). Aksi lain
    dibiarkan apa adanya (verify fallback), agar perilaku tak berubah.
    """
    if _contract is None or not text:
        return text
    if action not in ("gmail_send", "gmail_reply", "calendar_create"):
        return text
    _id = _squash(_pick(payload, "messageid", "message_id",
                        "eventid", "event_id", "id"), 60)
    if _id:
        return _contract.ok(text, tool="composio_actions", action=action, id=_id)
    # Terkirim menurut tool, tapi provider tak memberi id → tak terbukti.
    return _contract.unknown(
        text, tool="composio_actions", action=action,
        note=" [Sent, but the provider returned no message id — say it was "
             "sent without proof of delivery.]")


def _dispatch(action: str, params: dict, key: str, user: str) -> str:
    if action == "discover":
        return _discover(key, params)

    if action == "run_tool":
        tool = str(params.get("tool") or "").strip()
        raw_args = params.get("arguments")
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except Exception:
                raw_args = None
        if not tool:
            return ("run_tool butuh slug tool (mis. 'GMAIL_SEND_EMAIL') — "
                    "pakai 'discover' dulu untuk melihat daftarnya.")
        if not isinstance(raw_args, dict) or not raw_args:
            return ("run_tool butuh 'arguments' berisi parameter tool sesuai "
                    "schema-nya (lihat hasil discover).")
        ok, msg = _gate("run_tool", params, tool, raw_args)
        if not ok:
            return msg
        body = _execute_tool(key, tool, user, raw_args)
        return _render_payload(_unwrap(body, tool))

    spec = _ACTION_SPECS[action]
    slug = spec["slug"]
    toolkit = spec["toolkit"]

    missing = [f for f in _REQUIRE.get(action, []) if _blank(params.get(f))]
    if missing:
        return (f"Aksi '{action}' butuh: {', '.join(missing)}. "
                "Minta keterangan itu dulu, atau tarik datanya dengan "
                "gmail_search / calendar_list.")

    active = _active_toolkits(key, user)
    if active is not None and toolkit not in active:
        label = _TOOLKIT_LABELS.get(toolkit, toolkit)
        return (f"{label} belum tersambung. Buka panel Connections → kartu "
                f"Composio → sambungkan {label} dulu, lalu coba lagi.")

    friendly = _friendly_args(action, params, spec)
    schema = _tool_schema(key, slug)
    args, unmatched = _resolve(friendly, spec["map"], schema)
    bad = [f for f in _REQUIRE.get(action, []) if f in unmatched]
    if bad:
        return (f"Nama parameter {', '.join(bad)} tidak cocok dengan tool "
                f"{slug} yang terbit terbaru. Jalankan action 'discover' untuk "
                f"app ini, lalu pakai 'run_tool' dengan nama parameter yang benar.")

    # A misheard address is the failure that actually hurts: the sentence is
    # checked before anything is sent, and outward actions then wait for a
    # spoken "ya" with the very same arguments.
    bad_addr = _bad_email(action, params, args)
    if bad_addr:
        # GrokBot: nama yang diucapkan (bukan alamat) dicocokkan dengan
        # riwayat terkirim — DISARANKAN, tidak dipilih otomatis.
        hint = ""
        if action in ("gmail_send", "gmail_draft"):
            try:
                known = _sent_recipients(key, user)
                sug = [a for raw in _split_emails(params.get("to"))
                       if not _EMAIL_RE.match(raw)
                       for a in name_suggestions(raw, known)]
                if sug:
                    hint = (" Di riwayat terkirim ada: " + ", ".join(sug)
                            + " — tanyakan apakah salah satunya yang dimaksud.")
            except Exception as e:                       # noqa: BLE001
                print(f"[Composio] saran penerima gagal: {e}")
        return (f"Tidak jadi menjalankan: {bad_addr}. Minta pengguna "
                "mengulang alamat atau isi yang benar." + hint)
    if action in ("gmail_send", "gmail_draft"):
        try:
            params = dict(params)
            params["_recipient_note"] = recipient_note(
                params, _sent_recipients(key, user))
        except Exception as e:                           # noqa: BLE001
            print(f"[Composio] cek penerima gagal: {e}")
    ok, msg = _gate(action, params, slug, args)
    if not ok:
        return msg

    body = _execute_tool(key, slug, user, args)
    payload = _unwrap(body, slug)
    line = _success_line(action, params, payload)
    if action == "gmail_send" and line:
        _remember_recipients(params)
    if line:
        detail = _detail_line(payload)
        text = f"{line}\n{detail}" if detail else line
        return _attach_contract(action, text, payload)
    return _render_payload(payload)


def run(parameters: dict) -> str:
    """Voice entry point: one Composio action, executed and spoken back."""
    p = parameters or {}
    action = str(p.get("action") or "").strip().lower()
    if not action:
        return ("Sebutkan aksinya — mis. gmail_send, calendar_create, "
                "discover, atau run_tool.")
    known = set(_ACTION_SPECS) | {"discover", "run_tool"}
    if action not in known:
        return (f"Aksi '{action}' tidak dikenal. Pilihan: "
                f"{', '.join(sorted(known))}.")

    cfg = _config()
    key = str(cfg.get("api_key") or "").strip()
    if not key:
        return ("Composio belum disetel — buka panel Connections, klik kartu "
                "Composio, lalu masukkan API key dari dashboard.composio.dev.")
    user = str(cfg.get("user_id") or "aris").strip() or "aris"

    try:
        return _dispatch(action, p, key, user)
    except ComposioHTTPError as e:
        hint = ""
        if e.status == 404:
            hint = (" Kemungkinan nama tool/endpoint sudah berubah — jalankan "
                    "action 'discover' untuk melihat daftar tool terbaru, lalu "
                    "pakai 'run_tool' dengan slug yang benar.")
        elif e.status in (400, 422):
            hint = (" Periksa nama parameter terhadap schema tool lewat action "
                    "'discover'.")
        return f"Composio menolak: {e}{hint}"
    except ComposioError as e:
        return f"Composio bermasalah: {e}"


# ── "apakah ini benar-benar sudah dikerjakan?" ──────────────────────────────
#
# Plugin ini melaporkan SETIAP kegagalan sebagai kalimat Indonesia, bukan
# exception — jalur suara butuh itu. Tapi konsekuensinya: sebuah panggilan
# yang tidak mengirim email pun "berhasil" kembali ke pemanggil. Pekerjaan
# latar (main.py) memakai fungsi ini untuk membedakan "sudah terjadi" dari
# "tidak jadi" — supaya pengguna tidak pernah diberi tahu email terkirim
# ketika kenyataannya tidak.
#
# Hanya AWALAN kalimat yang cocok (posisi 0): isi payload yang kebetulan
# memuat kata "gagal" tidak boleh salah tuduhan. Bentuk kalimatnya milik
# plugin ini — lihat _dispatch() dan run() — jadi prefix-nya menyebut label
# persis seperti yang dihasilkan di sana. Kalimat konfirmasi ("KONFIRMASI
# DIPERLUKAN") BUKAN kegagalan: itu menunggu jawaban pengguna.
_NOT_DONE_PREFIXES = (
    # gagal HTTP/transport + kunci belum disetel
    "composio menolak", "composio bermasalah", "composio belum disetel",
    # koneksi belum ada (label dari _TOOLKIT_LABELS)
    "gmail belum tersambung", "google calendar belum tersambung",
    # schema tool berubah / argumen kurang / tidak dikenal
    "nama parameter", "tidak jadi menjalankan", "aksi '", "butuh:",
    "run_tool butuh", "sebutkan aksinya", "sebutkan app",
    "tidak ada tool yang cocok", "tidak dikenal",
    # bentuk umum untuk alat lain yang lewat jalur yang sama
    "tool ", "gagal", "error", "failed", "traceback", "exception",
    "ditolak", "tidak bisa", "tidak tersedia",
)


def looks_not_done(text: str) -> bool:
    """True kalau teks hasil ini berarti TIDAK ada yang terjadi."""
    low = " ".join(str(text or "").split()).lower()
    if not low:
        return True
    return low.startswith(_NOT_DONE_PREFIXES)
