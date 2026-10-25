"""
core/intent.py — the deterministic half of the two-tier router.

WHY THIS IS CODE AND NOT A PROMPT
    A tool description is a suggestion. The Live model was told "send writing
    work to the custom brain" and still answered "send an email to
    user@example.com" by opening WhatsApp and searching for a name
    nobody had said. The split therefore cannot live in the model's opinion
    of itself — it lives here, in a regex over the words the user actually
    spoke, and the dispatcher enforces it.

THE RULE
    A writing verb wins. "buka lalu kirim" contains both, and the safe
    reading is that something has to be written — that belongs to the brain,
    which can open apps too. Pure reading ("buka youtube", "cari berita")
    stays on the fast path, where latency is what the user feels.

    Deliberately no model call, no heuristic scoring: one regex, instant,
    predictable, and easy to extend with the next verb the user actually
    says.
"""
from __future__ import annotations

import re

WRITE_INTENT = re.compile(
    r"\b(kirim|kirimkan|mengirim|terkirim|buat|buatkan|membuat|bikin|bikinin|"
    r"tulis|tuliskan|menulis|edit|mengedit|ubah|mengubah|ganti|mengganti|"
    r"perbaiki|memperbaiki|susun|menyusun|rancang|merancang|jadwalkan|"
    r"menjadwalkan|simpan|menyimpan|draft|draf|balas|membalas|teruskan|"
    r"forward|hapus|menghapus|tambahkan|menambahkan|kreasikan|generate|"
    r"compose|send|write|create|update|delete|reply|rename|buatlah|"
    # 23 Okt — perluasan kosakata (permintaan bos): kerja, rekap, rapikan,
    # pindah, salin, unduh, pasang, bersihkan, ekspor/impor, terjemah, …
    r"lakukan|melakukan|kerjakan|mengerjakan|dikerjakan|"
    r"rekap|merekap|direkap|rangkum|merangkum|dirangkum|"
    r"ringkas|meringkas|diringkas|rapikan|merapikan|dirapikan|"
    r"pindah|pindahkan|memindahkan|dipindahkan|"
    r"salin|salinan|menyalin|disalin|unduh|mengunduh|diunduh|"
    r"pasang|memasang|dipasang|bersihkan|membersihkan|dibersihkan|"
    r"ekspor|mengekspor|impor|mengimpor|terjemah|terjemahkan|menerjemahkan|"
    r"gabung|gabungkan|menggabungkan|cadangkan|mencadangkan|"
    r"arsipkan|mengarsipkan|potong|memotong|kompres|mengompres|"
    # Inggris
    r"edit|edited|build|built|fix|fixed|refactor|deploy|save|saved|"
    r"add|remove|modify|modified|rewrite|summarize|translate|convert|"
    r"export|import|merge|install|download|upload|make|made)\b"
    # "schedule"/"book" hanya sebagai VERBA ("schedule a meeting"),
    # bukan sebagai kata benda ("check my schedule") — kalau tidak,
    # perintah ringan ikut dianggap pekerjaan berat.
    r"|\b(schedule|book)\s+(a|an|the|my|me)\b",
    re.IGNORECASE)

READ_INTENT = re.compile(
    r"\b(buka|bukakan|lihat|lihatlah|tampilkan|baca|bacakan|cari|carikan|"
    r"putar|refresh|segarkan|cek|periksa|berapa|siapa|kapan|dimana|"
    r"di mana|open|play|search|read|show|find|look)\b",
    re.IGNORECASE)

# The escape hatch. When the custom brain is down, the assistant offers the
# user a choice; if the user picks the fast model ("kamu saja", "gemini saja"),
# that must actually work — otherwise the writing verb in the sentence would
# route the request straight back to the brain that just failed, and the two
# of them would ask each other forever.
ALLOW_GEMINI = re.compile(
    r"(kamu\s+(saja|aja)|gemini\s+(saja|aja)|pakai\s+gemini|"
    r"kamu\s+yang|gemini\s+yang|kerjakan\s+(sendiri|kamu)|"
    r"you\s+do\s+it|use\s+gemini)",
    re.IGNORECASE)


def is_write_intent(text: str) -> bool:
    """True when the utterance asks for writing work.

    A writing verb always wins: a request that contains both verbs still has
    something to write, and the brain can read as well.
    """
    text = (text or "").strip()
    if not text:
        return False
    return bool(WRITE_INTENT.search(text))


def is_read_only(text: str) -> bool:
    """True when the utterance only reads/opens/searches (no writing verb)."""
    text = (text or "").strip()
    if not text:
        return False
    return bool(READ_INTENT.search(text)) and not WRITE_INTENT.search(text)


def allows_gemini(text: str) -> bool:
    """True when the user explicitly hands the job to the fast model.

    Used only after the brain has already failed and offered that choice —
    without it, "kamu saja yang kirim" contains a writing verb and would be
    sent back to the brain that just died.
    """
    return bool(ALLOW_GEMINI.search((text or "").strip()))


# A claim of completion without a tool call is the one failure the dispatcher
# cannot see: nothing was called, so there is nothing to intercept — the model
# simply says "sudah saya kirim" and moves on. This pattern catches that
# sentence so the brain can be handed the work for real.
_CLAIMS_DONE = re.compile(
    r"(\b(sudah|telah)\b[^.!?]{0,60}\b(kirim|mengirim|terkirim|buat|membuat|"
    r"dibuat|simpan|menyimpan|tersimpan|hapus|menghapus|ubah|mengubah|diubah|"
    r"edit|mengedit|jadwalkan|menjadwalkan|balas|membalas|draft)\b|"
    r"\b(terkirim|tersimpan|berhasil dikirim|berhasil dibuat|sudah selesai|"
    r"sent successfully|successfully sent|has been sent)\b)",
    re.IGNORECASE)


def claims_done(text: str) -> bool:
    """True when the reply claims a writing action was completed."""
    return bool(_CLAIMS_DONE.search((text or "").strip()))


# ── Phase 4 Slice 4: does the utterance ask about something remembered? ──────
# Recall used to depend on the model choosing to CALL recall_memory first. It
# usually did not: "ingat namaku siapa?" got an apology or an invention. A
# deterministic detector over the words the user actually said lets the
# application fetch the memory itself and hand it to the brain as a note.
#
# Deliberately narrow. Every false positive injects memory into a turn that
# did not ask for it, and memory that arrives unasked is exactly the
# "assistant jumps topics from memory" bug this project already fixed once
# (see _memory_for_prompt). The fetch itself is local and instant — BM25 plus
# the long-term dictionary — so the cost of being wrong is a few hundred
# characters, never a turn.
RECALL_INTENT = re.compile(
    r"(\bingat\w*|\bteringat\b|\btadi\b|\bkemarin\w*\b|\bbarusan\b|"
    r"\bsebelumnya\b|\bwaktu itu\b|\bnamaku\b|\bnama ku\b|\bnama saya\b|"
    r"\baku siapa\b|\bpassword\w*\b|\bkata sandi\b|\bsiapa nama\b|"
    r"\bapa nama\b|\bkita pernah\b|\bpernah (bicara|bilang|kasih tahu)\b|"
    r"\b(aku )?tinggal di mana\b|"
    r"\bremind|\bremember\b|\byesterday\b|\bearlier\b|\bjust now\b|"
    r"\bwhat.{0,12}\bname\b|\bwho am i\b|"
    r"\bmy (password|address|folder|project)\b)",
    re.IGNORECASE)


def _wants_recall(text: str) -> bool:
    """True when the utterance asks about something already remembered.

    Deterministic on purpose, same rule as the write/read split above: the
    decision of whether memory is fetched must not depend on the model's
    opinion of itself. Note the leading underscore — this is a detector for
    the application, not a public tool."""
    return bool(RECALL_INTENT.search((text or "").strip()))


# ── Phase 4 Slice 1: what KIND of turn is this, before any action? ───────────
# The router above answers "does this write?" — it does not answer "is this
# even FOR the assistant?". A user talking to another person ("kira-kira 3
# meter ya"), reading out text to be transcribed, saying goodbye, or just
# saying "hmm" got the same treatment as "kirim email ke raka": full tool
# list, full planning, a spoken answer. The classification is one regex pass
# over the words the user actually said — same philosophy as everything else
# in this file: the application decides, the model is not asked for its
# opinion of itself.
#
# The eleven types, in the order they are tested. First match wins, so the
# order IS the semantics:
#   recall(?)     a question about the past that happens to contain a writing
#                 verb ("kemarin aku kirim email ke siapa") is still a
#                 question — it reaches Slice 4's fetch, not the job queue.
#   work_request  a writing verb is present — the existing router keeps it,
#                 classification changes nothing for it (that is the point).
#   not_for_me    the user is talking to someone else — JARVIS goes quiet.
#   dictation     the user is reading text out — the text is material, not
#                 an order; commands inside it must not fire.
#   recall        asking about the past — feeds Slice 4's automatic fetch.
#   refusal       cancel / don't — cancelling is not work.
#   control       words about JARVIS itself: quiet, repeat, slower, louder.
#   farewell      goodbye — no tool is needed for a goodbye.
#   confirmation  "lanjut", "gas" — agreeing with what is on the table.
#   small_talk    greeting, chat, a joke — no tool is needed.
#   idle          thinking aloud ("hmm", "oh gitu") — owes nobody an answer.
#   unclear       everything else. The fallback deliberately keeps the old
#                 behaviour: unknown sentences still reach the brain with
#                 the full tool list, exactly as before this slice.
TURN_TYPES = (
    "work_request", "not_for_me", "dictation", "recall", "refusal",
    "control", "farewell", "confirmation", "small_talk", "idle", "unclear",
)

# Deliberately NARROW, like RECALL_INTENT: the cost of a false positive is
# high. A greeting mistaken for small_talk only saves a tool list; a question
# mistaken for not_for_me silences the assistant in the middle of real work.
_NOT_FOR_ME = re.compile(
    # "kirah-kira 3 meter ya" — a quantity followed by "ya" is something being
    # told to another person, not a request about a quantity. Both spellings
    # are common in speech: "kira-kira" and "kirah-kira".
    r"(\bkira(?:h)?-kira\b[^.!?]{0,24}\d[^.!?]{0,24}\s?ya\b|"
    r"\b(sekitar|kurang lebih)\b[^.!?]{0,24}\d[^.!?]{0,24}\s?ya\b|"
    # addressing someone else by name: "dek, ambilkan air"
    r"^\s*(?:dek|nak|bang|mas|kak|dik|mbak|pak|bu)\b|"
    # "ngomong ke pak bambu" — talking TO someone (not to JARVIS)
    r"\b(ngomong|bicara|ngomongin)\s+(ke|sama)\s+(?!kamu|jarvis|lu\b)|"
    # explicit: "bukan ke kamu"
    r"\bbukan\s+(ke|sama)\s+(kamu|jarvis|lu)\b)",
    re.IGNORECASE)

# Reading text out: the utterance is CONTENT. A command inside content
# ("...lalu kirim ke bos") is part of the text until the user says so.
_DICTATION = re.compile(
    r"\b(catat (?:ini|ya|nih|dulu|sebagai)|dengarkan baik|dengarin baik|"
    r"dicatat|dikte|diktasi|ikuti teks|tulis persis|salin persis|"
    r"teksnya adalah|aku bacakan|saya bacakan)\b",
    re.IGNORECASE)

_REFUSAL = re.compile(
    r"^\s*(batal|batalin|batalkan|ga jadi|gak jadi|nggak jadi|"
    r"gausah|nggak usah|ngga usah|jangan|udahan)\b",
    re.IGNORECASE)
# 22 Okt — negasi harus menang atas kata kerja tulis: "jangan kirim email"
# bukan permintaan mengirim. "stop" sengaja TIDAK di sini (itu kontrol).
_NEGATION = re.compile(
    r"^\s*(jangan|janganlah|gausah|nggak usah|ngga usah|"
    r"batal|batalin|batalkan)\b",
    re.IGNORECASE)

# Words about JARVIS itself, kept short: a long sentence containing "stop"
# is usually a job ("berhenti langganan"), not a control word.
_CONTROL = re.compile(
    r"\b(diam|berhenti|stop|ulangi|ulangin|ulang lagi|pelan-pelan|"
    r"keras-keras|naikkan suara|kecilkan suara|lebih pelan|lebih keras|"
    r"bicaranya)\b",
    re.IGNORECASE)

_FAREWELL = re.compile(
    r"\b(makasih|terima kasih|sampai jumpa|sampe jumpa|dadah|dulu ya|"
    r"cukup sekian|permisi dulu|sudah cukup)\b",
    re.IGNORECASE)

_CONFIRMATION = re.compile(
    r"^\s*(lanjut|lanjutkan|lanjutin|silakan|silahkan|gas|setuju|"
    r"boleh|ayo|ayolah|kerjakan|sudah cukup)\b",
    re.IGNORECASE)

_SMALL_TALK = re.compile(
    r"\b(hai|halo|hello|selamat|apa kabar|kabar baik|siapa kamu|"
    r"kamu siapa|cerita|lelucon|lucu|bosan|ngobrol|kenalan)\b",
    re.IGNORECASE)

# Thinking aloud. "oke" is NOT here on purpose: per spec it is unclear, not
# idle — an "oke" in the middle of work is ambiguous, and ambiguous must not
# silently cancel anything.
_IDLE = re.compile(
    r"^\s*(hmm+|hm+|oalah|oh gitu|oh begitu|begitu|begitu ya|ah\b)\s*$",
    re.IGNORECASE)


# A question about the past outranks a writing verb: "kemarin aku kirim
# email ke siapa" contains "kirim" but is a question, not a job — answering
# it with work would be answering a different question. The reverse guard
# ("balas pesan tadi" — a job that merely mentions the past) keeps jobs
# with "tadi" on the fast path.
_QUESTION = re.compile(
    r"(\?$|\b(siapa|apa|kapan|di ?mana|berapa|mengapa|kenapa|gimana|"
    r"bagaimana|why|who|what|when|where|how)\b)",
    re.IGNORECASE)


def _asks_question(text: str) -> bool:
    text = (text or "").strip()
    return bool(_QUESTION.search(text))


def classify_turn(text: str) -> str:
    """Classify the utterance into one of TURN_TYPES, deterministically.

    The enforcement layer (main.py) uses this BEFORE the router's
    write/read decision is acted on: a turn that is not for the assistant
    must not receive a tool list, and must not be answered at all. Writing
    verbs still win first — "makasih, sekalian kirim laporan" is work,
    because it contains one; that keeps the existing router untouched.
    """
    text = (text or "").strip()
    if not text:
        return "unclear"
    # A past question that happens to contain a writing verb is still a
    # question about the past — it must reach the recall ladder, not the
    # job queue.
    if _wants_recall(text) and _asks_question(text):
        return "recall"
    # 22 Okt — negasi diperiksa SEBELUM kata kerja tulis (bug: "jangan kirim
    # email itu" dulu masuk work_request dan bisa memicu aksi).
    if _NEGATION.match(text):
        return "refusal"
    if is_write_intent(text):
        return "work_request"
    if _NOT_FOR_ME.search(text):
        return "not_for_me"
    if _DICTATION.search(text):
        return "dictation"
    if _wants_recall(text):
        return "recall"
    if _REFUSAL.search(text):
        return "refusal"
    # Control words only count in short utterances: "diam" is control,
    # "diamkan dulu notifikasinya" is a job.
    if len(text.split()) <= 6 and _CONTROL.search(text):
        return "control"
    # A goodbye is short and question-free; "makasih, tolong cari berita"
    # after a read verb is still a request.
    if (len(text.split()) <= 5 and "?" not in text
            and _FAREWELL.search(text)):
        return "farewell"
    if _CONFIRMATION.search(text):
        return "confirmation"
    if (len(text.split()) <= 4 and "?" not in text
            and not is_read_only(text)
            and _SMALL_TALK.search(text)):
        return "small_talk"
    if _IDLE.match(text):
        return "idle"
    # 22 Okt — ucapan sangat pendek yang tidak ambigu: "ya"/"iya" adalah
    # konfirmasi, "tidur"/"sleep" adalah kontrol. Tanpa ini keduanya jatuh ke
    # "unclear" dan menerima daftar tool penuh.
    if len(text.split()) <= 2:
        low = text.lower().strip(" .!?,")
        if low in ("ya", "iya", "yes", "yup"):
            return "confirmation"
        if low in ("tidur", "sleep", "istirahat"):
            return "control"
    return "unclear"
