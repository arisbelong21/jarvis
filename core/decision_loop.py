"""
core/decision_loop.py — SATU pintu keputusan untuk setiap perintah dan tool.

Kenapa ada
    Sebelum ini setiap jalur masuk punya aturannya sendiri:
      • suara/Gemini Live  → main.py::_execute_tool  (permissions.decide)
      • otak kustom        → _execute_tool(_internal=True) (sama, tapi persetujuan
                             dibaca dari _live_utterance dengan substring)
      • agent runner       → core/agent/runner.py (permissions.decide sendiri)
      • Telegram /open /click → plugins/_telegram_ops.py (TANPA gerbang, dan
                             hasil open_app DITIMPA tanpa bukti)
      • dashboard          → antrean teks → sesi Live → _execute_tool
    Akibatnya aturan izin, whitelist klik, dan verifikasi hasil berbeda-beda
    per jalur. Modul ini menyatukan urutan yang sama untuk semua jalur:

        intent → izin/trust (gate) → panggil tool → verifikasi hasil → balasan

    main.py tetap memegang eksekusi async-nya (cabang tool bawaan terlalu
    banyak untuk dipindah tanpa menulis ulang), tetapi keputusan izin dan
    verifikasi hasil SELALU lewat gate() dan verify() di sini. Jalur sinkron
    (Telegram, agent runner) memakai dispatch() yang menjalankan seluruh
    putaran.

Prinsip
    • deny tetap menang atas segalanya (permissions.py tidak diubah urutannya).
    • Whitelist klik "coba lagi" hanya menurunkan confirm → allow untuk klik
      yang TIDAK merusak (lihat config/decision_rules.json).
    • Klaim berhasil hanya boleh keluar bila verify() mengatakan terverifikasi.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from core import contract as _contract          # Audit 26 Okt: verifikasi berbasis kontrak

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "config" / "decision_rules.json"

_DEFAULT_RULES = {
    "retry_click": {
        "enabled": True,
        "button_words": ["retry", "try again", "coba lagi", "ulangi", "ulang",
                         "rerun", "re-run", "resume", "reconnect", "continue",
                         "lanjutkan", "regenerate", "run again",
                         "jalankan lagi"],
        "agent_windows": ["workbuddy", "opencode", "open code", "cursor",
                          "visual studio code", "vs code", "vscode",
                          "windsurf", "trae", "copilot", "claude",
                          "codebuddy", "zed"],
        # Audit 25 Okt (T-3): default True — whitelist hanya untuk jendela
        # agen koding. Deskripsi klik berasal dari model; tanpa syarat ini
        # prompt injection cukup menyelipkan kata "retry" untuk dapat izin.
        "require_agent_window": True,
        "forbidden_words": ["delete", "hapus", "remove", "discard", "buang",
                            "reset", "uninstall", "format", "send", "kirim",
                            "pay", "bayar", "purchase", "beli"],
    },
    "approval_words": ["ya", "iya", "yes", "yup", "lanjut", "lanjutkan",
                       "boleh", "silakan", "silahkan", "setuju", "oke", "ok",
                       "okay", "gas", "gaskan", "betul", "benar", "sip"],
}

_rules_cache: list = [None, 0.0]          # [rules, mtime]


def load_rules() -> dict:
    """Aturan dari config/decision_rules.json; rusak/hilang → bawaan."""
    try:
        mtime = RULES_PATH.stat().st_mtime
    except OSError:
        return _DEFAULT_RULES
    if _rules_cache[0] is not None and _rules_cache[1] == mtime:
        return _rules_cache[0]
    try:
        raw = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        rules = dict(_DEFAULT_RULES)
        if isinstance(raw, dict):
            for k, v in raw.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict) and isinstance(rules.get(k), dict):
                    merged = dict(rules[k])
                    merged.update({a: b for a, b in v.items()
                                   if not a.startswith("_")})
                    rules[k] = merged
                else:
                    rules[k] = v
    except Exception:                                    # noqa: BLE001
        rules = _DEFAULT_RULES
    _rules_cache[0], _rules_cache[1] = rules, mtime
    return rules


def _has_phrase(text: str, phrases) -> bool:
    """Frasa utuh (batas kata), bukan substring: 'ya' tidak cocok 'saya'."""
    low = (text or "").lower()
    for p in phrases or ():
        p = str(p).strip().lower()
        if p and re.search(r"(?<![\w])" + re.escape(p) + r"(?![\w])", low):
            return True
    return False


# 22 Okt — kata yang membatalkan persetujuan meski ada "ya/ok" di kalimat.
_APPROVAL_BLOCK = re.compile(
    r"\b(jangan|jgn|tapi|kecuali|belum|tunggu|batal|nanti)\b", re.I)


# ── intent ───────────────────────────────────────────────────────────────────

def is_approval(text: str) -> bool:
    """True bila ucapan berisi kata setuju sebagai KATA UTUH.

    Versi lama di main.py memakai `w in said`, sehingga "saya", "hanya",
    "bayar" (mengandung "ya") dan "token"/"book" (mengandung "ok") dianggap
    persetujuan.

    22 Okt — kalimat bersyarat/negatif BUKAN izin: "ya tapi jangan kirim",
    "jangan kirim dulu ya", "tunggu, ya nanti" semuanya mengandung kata
    setuju tetapi maksudnya menolak/menunda.
    """
    if _APPROVAL_BLOCK.search(text or ""):
        return False
    return _has_phrase(text, load_rules().get("approval_words") or ())


def is_retry_click(tool: str, args: dict | None) -> bool:
    """Klik tombol coba-lagi/retry pada jendela (agen koding) yang error."""
    rc = load_rules().get("retry_click") or {}
    if not rc.get("enabled", True):
        return False
    if (tool or "").strip().lower() != "computer_control":
        return False
    a = args or {}
    action = str(a.get("action") or "").strip().lower()
    if action not in ("screen_click", "screen_find"):
        return False
    desc = " ".join(str(a.get(k) or "") for k in ("description", "title"))
    if not _has_phrase(desc, rc.get("button_words")):
        return False
    if _has_phrase(desc, rc.get("forbidden_words")):
        return False
    if rc.get("require_agent_window") and not _has_phrase(
            desc, rc.get("agent_windows")):
        return False
    return True


# ── terminal: perintah baca vs perintah yang mengubah sistem ────────────────
# Perintah HANYA-BACA boleh jalan bebas. Selain itu (hapus, format, registry,
# shutdown, instal, git push/reset --hard, perintah tak dikenal, rangkaian
# dengan pengalihan ">") WAJIB dikonfirmasi Aris lewat mekanisme konfirmasi
# yang sudah ada (main.py::_pending_confirm / AgentRunner pause). Konservatif:
# yang tidak dikenali = "change".

_READ_CMDS = {
    "dir", "ls", "type", "cat", "more", "echo", "where", "which", "whoami",
    "hostname", "ipconfig", "ifconfig", "ping", "tasklist", "systeminfo",
    "ver", "pwd", "cd", "tree", "findstr", "find", "grep", "rg", "head",
    "tail", "wc", "netstat", "nslookup", "get-childitem", "gci",
    "get-content", "gc", "get-process", "gps", "get-location", "gl",
    "get-date", "get-command", "gcm", "select-string", "sls", "test-path",
    "get-item", "gi", "get-service", "gsv", "resolve-path", "measure-object",
    "get-computerinfo", "date", "uname", "df", "du", "free", "uptime",
    # Audit 25 Okt (T-2): `env`/`printenv`/`set` DIBUANG dari daftar baca.
    # Ketiganya men-dump environment yang sering memuat token/kunci; hasil
    # dump masuk ke konteks model dan bisa bocor lewat tool external
    # berikutnya. Tanpa entri ini mereka jatuh ke "change" (wajib konfirmasi).
}
_READ_SUB = {
    "git": {"status", "log", "diff", "show", "branch", "remote", "rev-parse",
            "ls-files", "blame", "describe", "tag"},
    "pip": {"list", "show", "freeze", "--version", "-v", "check"},
    "npm": {"ls", "list", "--version", "-v", "view", "outdated"},
    "node": {"--version", "-v"},
    "python": {"--version", "-v", "-V"},
    "py": {"--version", "-V", "-0"},
    "opencode": {"--version", "--help"},
    "code": {"--version", "--list-extensions"},
}
_DESTRUCTIVE_CMD = re.compile(
    r"(?<![\w-])(del|erase|rd|rmdir|rm|remove-item|ri|format|diskpart|"
    r"reg|regedit|set-itemproperty|new-itemproperty|remove-itemproperty|"
    r"shutdown|restart-computer|stop-computer|logoff|bcdedit|takeown|icacls|"
    r"cipher|vssadmin|wbadmin|sfc|dism|chkdsk|mkfs|dd|"
    r"winget|choco|scoop|msiexec|setx|"
    r"taskkill|stop-process|kill|pkill|sc|net|netsh|"
    r"move|mv|ren|rename|rename-item|move-item|"
    r"invoke-webrequest|iwr|curl|wget|invoke-expression|iex|start-process)"
    r"(?![\w-])", re.I)
_DESTRUCTIVE_PHRASE = re.compile(
    r"(git\s+(push|reset\s+--hard|clean|checkout\s+--|rebase|filter-branch)|"
    r"pip\s+(install|uninstall)|npm\s+(install|i|uninstall|publish)\b.*-g|"
    r"npm\s+(publish|uninstall)|--force|-rf\b|/s\s+/q)", re.I)


# 22 Okt — perintah yang menjalankan kode sewenang-wenang. Semua ini dulu
# diklasifikasikan "change" (tetap minta konfirmasi), tetapi labelnya harus
# jujur: pengguna berhak tahu bahwa perintahnya menjalankan kode apa pun.
_DANGER_EXTRA = re.compile(
    r"(-enc\b|-encodedcommand|frombase64string|\biex\b|invoke-expression|"
    r"\bcertutil\b|\bbitsadmin\b|\bwmic\b[^|;&]*\bcall\s+create\b|"
    r"\bpython(?:3)?\s+-c\b|\bnode\s+-e\b)", re.I)


# Audit 25 Okt (T-2): path berkas rahasia proyek. Perintah apa pun yang
# menyebut salah satunya — walau hanya `type`/`cat`/`findstr` — dinaikkan ke
# "change" supaya dikonfirmasi. Isinya kunci API, secret cloud link, trust
# table, dan kunci privat TLS; sekali terbaca ke konteks model, ia bisa
# ikut terkirim oleh tool external berikutnya.
_SENSITIVE_PATH = re.compile(
    r"(api_keys\.json|tool_trust\.json|agent_state\.json|cloud_state|"
    r"link\.json|[/\\]certs[/\\]|\.key\b|\.pem\b|id_rsa|\b\.env\b|credentials)",
    re.I)


def classify_command(command: str) -> str:
    """'read' | 'change' | 'destructive' untuk satu baris perintah terminal."""
    cmd = (command or "").strip()
    if not cmd:
        return "change"
    if (_DESTRUCTIVE_PHRASE.search(cmd) or _DESTRUCTIVE_CMD.search(cmd)
            or _DANGER_EXTRA.search(cmd)):
        return "destructive"
    if _SENSITIVE_PATH.search(cmd):
        return "change"                     # membaca/menyentuh berkas rahasia
    if re.search(r"(^|[^2])>|>>|`|\$\(", cmd):
        return "change"                     # menulis berkas / substitusi
    for seg in re.split(r"\|\||&&|[|;&]", cmd):
        toks = seg.strip().split()
        if not toks:
            continue
        head = re.sub(r"\.(exe|cmd|bat|com)$", "", toks[0].strip('"\'').replace("\\", "/").split("/")[-1].lower())
        if head in _READ_CMDS:
            continue
        sub = _READ_SUB.get(head)
        if sub and len(toks) > 1 and toks[1].lower() in sub:
            continue
        return "change"
    return "read"


# ── izin ─────────────────────────────────────────────────────────────────────

def gate(tool: str, args: dict | None = None, *, config: dict | None = None,
         known: bool | None = True, explicit: bool = False,
         internal: bool = False) -> tuple[str, str]:
    """('allow'|'confirm'|'deny', alasan) — satu-satunya tempat keputusan izin.

    explicit=True: perintah diketik persis oleh pengguna (Telegram /open,
    /click) — perintah itu sendiri adalah persetujuannya, kecuali tool
    destruktif atau yang di-deny.

    internal=True: panggilan datang dari otak custom, bukan dari Gemini.
    Dipakai untuk melewati konfirmasi tool "tangan" — pengguna sudah memilih
    otak lewat tawaran, jadi bertanya lagi hanya menggandakan pertanyaan.
    """
    from core.agent import permissions
    decision, why = permissions.decide(tool, config, known=known, args=args,
                                       internal=internal)
    t = (tool or "").strip().lower()
    if t == "run_command" and decision != "deny":
        kind = classify_command(str((args or {}).get("command") or ""))
        if kind == "read":
            return "allow", "perintah terminal hanya-baca"
        # Mengubah sistem: SELALU konfirmasi — tidak bisa di-trust dan tidak
        # bisa dilewati oleh `explicit` (salah ketik = kerusakan nyata).
        return "confirm", ("perintah terminal yang mengubah sistem"
                           if kind == "change" else
                           "perintah terminal DESTRUKTIF (hapus/format/"
                           "registry/shutdown/instal/git push)")
    if decision != "confirm":
        return decision, why
    if is_retry_click(tool, args):
        return "allow", "whitelist: klik tombol coba-lagi/retry (tidak merusak)"
    if explicit and permissions.classify(tool) != "destructive":
        return "allow", "perintah eksplisit pengguna (diketik langsung)"
    return decision, why


# ── verifikasi ───────────────────────────────────────────────────────────────

_FAIL_PREFIX = ("error", "tool '", "unknown tool", "failed", "could not",
                "gagal", "element not found", "not_found", "nothing called",
                "a window appeared, but", "unknown app", "ran the launcher",
                "no application name", "sir, the '", "plugin '", "action '",
                "composio menolak", "composio bermasalah", "unknown app",
                # 22 Okt — penolakan akses adalah kegagalan, bukan "tidak
                # diketahui": model sempat melaporkannya sebagai berhasil.
                "access denied", "permission denied", "denied",
                "akses ditolak", "tidak diizinkan")

UNVERIFIED_NOTE = (" [UNVERIFIED — do not tell the user this succeeded. Say "
                   "plainly what actually happened, and what to do next.]")


def verify(tool: str, args: dict | None, result) -> tuple[bool | None, str]:
    """(terverifikasi?, teks hasil). None = tool ini tidak punya bukti khusus.

    open_app      — hanya "Opened …" yang terverifikasi (open_app sendiri
                    mencocokkan proses/jendela dengan kamus aplikasi).
    screen_click  — "Clicked …" = klik terjadi pada elemen yang ditemukan.
    lainnya       — gagal bila teksnya diawali penanda kegagalan.

    Audit 26 Okt — KONTRAK lebih dulu: tool yang sudah dimigrasi menempel
    bukti terstruktur (core/contract.py). Bila sentinel ada, field `ok`-nya
    adalah kebenaran dan heuristik cocok-teks di bawah dilewati. Tool yang
    belum dimigrasi tidak menempel apa pun → perilaku lama tak berubah.
    """
    text = "" if result is None else str(result)
    payload, clean = _contract.parse(text)
    if payload is not None:
        ok   = payload.get("ok")                 # True | False | None
        note = payload.get("note")
        out  = clean + note if note else clean
        if ok is None and not note:
            out = clean + UNVERIFIED_NOTE
        return ok, out
    low = text.strip().lower()
    if "[failed —" in low:
        # Sudah ditandai gagal oleh run_with_retry — satu catatan cukup.
        return False, text
    t = (tool or "").strip().lower()
    if t == "open_app":
        ok = low.startswith("opened ")
        return ok, text if ok or "[unverified" in low else text + UNVERIFIED_NOTE
    if t == "computer_control":
        act = str((args or {}).get("action") or "").lower()
        if act == "screen_click":
            ok = low.startswith("clicked")
            if ok and "did not visibly change" in low:
                return None, text + (" [The click happened but the screen did "
                                     "not change — say so; do not claim the "
                                     "button worked.]")
            return ok, text if ok or UNVERIFIED_NOTE in text else text + UNVERIFIED_NOTE
    if t == "run_command":
        m = re.search(r"exit_code:\s*(-?\d+)", text)
        ok = bool(m) and m.group(1) == "0"
        return ok, text if ok else text + UNVERIFIED_NOTE
    if t == "composio_actions":
        act = str((args or {}).get("action") or "").lower()
        if act in ("gmail_send", "gmail_reply", "calendar_create"):
            if low.startswith(("konfirmasi diperlukan", "konfirmasi")):
                return None, text          # menunggu jawaban, bukan gagal
            ok = bool(re.search(r"(message_id|event_id|id)\s*:", low)) or \
                low.startswith(("email terkirim", "balasan terkirim",
                                "acara “"))
            has_id = bool(re.search(r"(message_id|event_id):", low))
            if ok and not has_id:
                return None, text + (" [Sent, but the provider returned no "
                                     "message id — say it was sent without "
                                     "proof of delivery.]")
            return ok, text if ok else text + UNVERIFIED_NOTE
    written = _written_path(t, args, text)
    if written is not None:
        exists = written.exists()
        if not exists:
            return False, text + (f" [UNVERIFIED — the file {written} does not "
                                  "exist after the write. Do not say it was "
                                  "saved.]")
    if low.startswith(_FAIL_PREFIX):
        return False, text
    if written is not None:
        return True, text
    return None, text


def strip_contract(text):
    """Buang sentinel kontrak dari teks (dipakai di jalur except sebagai
    pengaman agar bukti mesin tak pernah bocor ke model/pengguna)."""
    return _contract.strip(text)


_WRITE_ACTIONS = {"write", "create", "save", "edit", "replace", "build",
                  "optimize", "append", "new"}
_PATH_IN_TEXT = re.compile(r"([A-Za-z]:\\[^\s\"'<>|*?]+\.\w{1,6}|/(?:home|Users|tmp)/[^\s\"'<>|*?]+\.\w{1,6})")


def _written_path(tool: str, args: dict | None, text: str):
    """Path absolut yang SEHARUSNYA ada setelah tool penulis berkas, atau None.

    Hanya untuk tool penulis berkas dengan aksi menulis, dan hanya path
    ABSOLUT (path relatif diselesaikan di dalam tool masing-masing)."""
    a = args or {}
    if tool not in ("file_controller", "code_helper", "docx_writer",
                    "excel_writer", "dev_agent"):
        return None
    act = str(a.get("action") or "write").lower()
    if tool in ("file_controller", "code_helper") and act not in _WRITE_ACTIONS:
        return None
    cand = [str(a.get(k) or "") for k in ("output_path", "file_path", "path")]
    m = _PATH_IN_TEXT.search(text or "")
    if m:
        cand.append(m.group(1).rstrip(".,;:)"))
    for c in cand:
        if c and (re.match(r"^[A-Za-z]:\\", c) or c.startswith("/")):
            try:
                return Path(c)
            except Exception:                            # noqa: BLE001
                return None
    return None


# ── kegagalan: sementara vs permanen ─────────────────────────────────────────

_TRANSIENT = re.compile(
    r"(timed? ?out|timeout|temporar|connection (reset|aborted|refused)|"
    r"remote end closed|\b(502|503|504)\b|service unavailable|bad gateway|"
    r"try again later|econnreset|network is unreachable|name resolution|"
    r"getaddrinfo|ssl: |eof occurred|server disconnected)", re.I)
_PERMANENT = re.compile(
    r"(\b(401|403|404)\b|unauthori[sz]ed|forbidden|invalid api key|"
    r"not found|no such|quota|permission denied|tidak dikenal|unknown)", re.I)


def classify_failure(text) -> str:
    """'ok' | 'transient' | 'permanent' dari teks hasil/exception."""
    t = str(text or "")
    low = t.strip().lower()
    if not low.startswith(_FAIL_PREFIX):
        return "ok"
    if _PERMANENT.search(low[:300]):
        return "permanent"
    if _TRANSIENT.search(low[:300]):
        return "transient"
    return "permanent"


def retry_safe(tool: str, args: dict | None = None) -> bool:
    """Boleh diulang otomatis? HANYA tool baca — mengulang kirim/klik/buka
    bisa menggandakan efek (email terkirim dua kali)."""
    from core.agent import permissions
    t = (tool or "").strip().lower()
    if t == "run_command":
        return classify_command(str((args or {}).get("command") or "")) == "read"
    if t == "composio_actions":
        act = str((args or {}).get("action") or "").lower()
        return act in ("gmail_search", "gmail_read", "calendar_list",
                       "calendar_get", "calendar_find", "discover")
    return permissions.classify(t) == "read"


def run_with_retry(tool: str, args: dict | None, fn, *, attempts: int = 3,
                   backoff: float = 1.0, sleep=time.sleep):
    """Jalankan fn(); ulangi kegagalan SEMENTARA (maks attempts-1 kali,
    jeda 1 s lalu 2 s) hanya untuk tool yang aman diulang. Exception tidak
    ditelan: setelah percobaan habis ia dikembalikan sebagai teks kegagalan
    yang jelas, bukan dibuang."""
    safe = retry_safe(tool, args)
    last = None
    for i in range(max(1, attempts if safe else 1)):
        try:
            last = fn()
        except Exception as e:                           # noqa: BLE001
            last = f"ERROR: {type(e).__name__}: {e}"
        kind = classify_failure(last)
        if kind != "transient" or not safe or i == attempts - 1:
            break
        print(f"[Decision] {tool}: gangguan sementara, ulang ke-{i + 1} "
              f"setelah {backoff * (2 ** i):.0f} s")
        sleep(backoff * (2 ** i))
    if classify_failure(last) != "ok" and isinstance(last, str) \
            and "[FAILED" not in last:
        kind = classify_failure(last)
        last = last + (" [FAILED — " + ("temporary problem, retried"
                       if kind == "transient" and safe else
                       "permanent problem") + ". Tell the user clearly what "
                       "failed and what they can do; never stay silent.]")
    return last


# ── satu putaran penuh (jalur sinkron: Telegram, agent, dashboard langsung) ──

def dispatch(tool: str, args: dict | None, execute, *, source: str,
             config: dict | None = None, known: bool | None = True,
             explicit: bool = False) -> dict:
    """intent → gate → execute → verify → balasan, untuk satu panggilan tool.

    execute(tool, args) -> str  dijalankan HANYA bila gate mengizinkan.
    Mengembalikan dict: decision, why, result, verified, reply.
    """
    args = dict(args or {})
    t0 = time.monotonic()
    decision, why = gate(tool, args, config=config, known=known,
                         explicit=explicit)
    if decision != "allow":
        reply = (f"'{tool}' tidak dijalankan — {why}." if decision == "deny"
                 else f"'{tool}' butuh konfirmasi dulu ({why}).")
        _audit(tool, args, source, decision, False, t0, reply)
        return {"decision": decision, "why": why, "result": "",
                "verified": False, "reply": reply}
    result = run_with_retry(tool, args, lambda: execute(tool, args))
    verified, text = verify(tool, args, result)
    note_outcome(tool, verified, text)
    _audit(tool, args, source, decision, verified is not False, t0, text)
    return {"decision": decision, "why": why, "result": str(result or ""),
            "verified": verified, "reply": text.replace(UNVERIFIED_NOTE, "")}


def _audit(tool, args, source, decision, ok, t0, summary) -> None:
    try:
        from core.agent import audit
        audit.record(tool, args, actor=str(source or "unknown"),
                     decision=decision, ok=bool(ok),
                     ms=int((time.monotonic() - t0) * 1000),
                     summary=str(summary)[:220])
    except Exception:                                    # noqa: BLE001
        pass


# ── jembatan ke balasan (Telegram relay) ─────────────────────────────────────
# Relay Telegram meneruskan KALIMAT MODEL, bukan hasil tool. Kalau model
# berkata "OpenCode sudah terbuka" padahal open_app melaporkan jendela lain,
# klaim itu dulu sampai ke ponsel apa adanya. Hasil terakhir dicatat di sini
# supaya relay bisa mengoreksinya.

_last_outcome: dict = {}

_CLAIM = re.compile(
    r"(\b(sudah|telah|berhasil|udah)\b[^.!?]{0,50}\b(buka|dibuka|membuka|"
    r"terbuka|klik|diklik|mengklik|jalan|dijalankan|menjalankan|kirim|"
    r"dikirim|terkirim)\w*|\b(opened|launched|clicked|is open|is now open|"
    r"done|sent)\b)", re.IGNORECASE)


def note_outcome(tool: str, verified: bool | None, text: str) -> None:
    """Catat hasil tool terakhir (dipanggil oleh semua jalur eksekusi)."""
    _last_outcome.clear()
    _last_outcome.update({"tool": tool, "verified": verified,
                          "text": str(text or "").replace(UNVERIFIED_NOTE, ""),
                          "ts": time.time()})


def last_outcome(since: float = 0.0) -> dict | None:
    if _last_outcome and float(_last_outcome.get("ts") or 0) >= since:
        return dict(_last_outcome)
    return None


def claims_success(text: str) -> bool:
    return bool(_CLAIM.search(text or ""))


def honest_reply(answer: str, since: float = 0.0) -> str:
    """Balasan yang aman diteruskan: klaim berhasil diganti bila hasil tool
    terakhir dalam giliran ini TIDAK terverifikasi."""
    out = last_outcome(since)
    if not out or out.get("verified") is not False:
        return answer
    if not claims_success(answer):
        return answer
    return ("Belum berhasil dipastikan. Hasil sebenarnya: "
            + (out.get("text") or "tidak ada bukti").strip())
