"""
core/custom_brain.py — the custom-API brain for the hybrid voice engine.

In hybrid mode the Gemini Live session is only ears and mouth: it transcribes
what the user says, hands the words to the `brain` tool, and speaks the reply
word for word. Everything in between — understanding, deciding, calling
tools, composing the answer — happens on the user's own provider (GLM,
Claude, GPT, …) through providers/llm_openai.py, with the same tool
declarations the Live session would otherwise have carried itself.

WHY THIS SHAPE
    The Live model is excellent at audio (fast, interruptible, no download)
    and unreliable at instruction-following. The custom model is the
    opposite. So the session keeps what Gemini is good at and moves the
    thinking to the model the user actually chose. The mis-heard sentence is
    still Gemini's transcription — but the decision of what to DO with it,
    and every argument a tool receives, now comes from the custom brain, and
    every tool still passes the app's own gates on the way out.

WHAT ONE TURN LOOKS LIKE
    utterance → llm.complete(messages, tools)
        ├─ no tool_calls → final text → spoken by the Live mouth
        └─ tool_calls  → executed through the app's own dispatcher
                          (actions, plugins, MCP, confirmations — all of it)
                          → results appended as tool messages → loop again
    The loop is capped at MAX_STEPS; a rolling history keeps the last few
    exchanges so "kirim lagi yang tadi" still works.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

_FAIL_MARKERS = ("error", "gagal", "failed", "traceback", "tidak bisa",
                 "not found", "tidak ditemukan", "ditolak", "exception")

# 22 Okt — batas keras langkah per giliran. Config boleh menurunkan, tidak
# menaikkan: 12-13 langkah tanpa hasil adalah "thrash", bukan kerja.
_HARD_STEP_CAP = 8

# 22 Okt — klaim perubahan tanpa satu pun tool dijalankan adalah klaim palsu.
_CLAIM_RE = re.compile(
    r"\b(sudah|telah|berhasil)\b.{0,40}\b(ganti|diganti|dibuat|dihapus|"
    r"dikirim|dipindah|disalin|diubah|ditemukan|tidak ada)\b", re.I)

# 22 Okt — sapaan bukan tugas. Kalau model menjawab sapaan dengan "siap saya
# kerjakan", kalimat itu diganti sapaan singkat (deterministik, bukan prompt).
_GREET_RE = re.compile(
    r"^\s*(hey|hi|halo|hallo|hello|pagi|siang|malam|selamat|makasih|"
    r"terima kasih|thanks)\b", re.I)
_TASK_ACK_RE = re.compile(
    r"\bsiap\b.{0,16}\b(saya\s+)?(kerjakan|kerjain|proses|mengerjakan)\b", re.I)

# Phase 4 Slice 4: tag for facts the application fetched on its own because
# the user asked about something remembered. Defined once, here at the
# injection point; prompt.txt explains it to the model.
RECALL_NOTE_TAG = "[INGATAN_OTOMATIS]"


def _looks_failed(text: str) -> bool:
    """Deteksi cepat hasil tool yang gagal, untuk memicu koreksi diri."""
    low = (text or "").strip().lower()
    if not low:
        return True
    if low.startswith(("error", "failed", "gagal", "traceback")):
        return True
    return any(m in low[:160] for m in _FAIL_MARKERS)


_fallback_hook = None


def set_fallback_hook(fn) -> None:
    """Daftarkan penerima kabar "provider cadangan yang menjawab".

    23 Okt: pengguna melihat UI tetap menampilkan model pilihannya padahal
    yang menjawab provider lain (rotasi berjalan diam-diam). Aplikasi
    memakai hook ini untuk memindahkan badge + menulis satu baris log,
    sehingga tampilan selalu sama dengan kenyataan.
    """
    global _fallback_hook
    _fallback_hook = fn


class FallbackLLM:
    """Several providers, tried in order, until one answers.

    One provider is a single point of failure: the log showed the active
    model "too slow" and the backup "out of quota" while a third one was
    answering in 4 seconds — but the brain had been built with the slow one
    only, so the turn died on the clock instead of moving to the provider
    that worked. The first provider that answers wins; the failure of one is
    a line in the console, not the end of the turn.
    """

    def __init__(self, providers):
        self._providers = list(providers or [])   # [(pid, llm), …]
        self._cool: dict[str, float] = {}
        # Alasan kegagalan terakhir per provider. Tanpa ini, aplikasi
        # hanya tahu model yang DIPILIH (config) dan bisa menjawab
        # "aman" padahal providernya sedang 429 (kejadian 24 Okt).
        self.last_error: dict[str, str] = {}         # pid -> ready-at timestamp
        self.last = ""

    COOLDOWN = 300.0      # a failed provider rests five minutes, then is
                          # tried again — long enough to stop paying its
                          # timeout on every turn, short enough to notice
                          # when it comes back.

    def _order(self):
        """Providers that are not resting first, in their original order.

        Measured: with a broken first provider and a quota-limited second,
        every turn spent ~49s reaching the third one. Resting a provider that
        just failed makes the next turn go straight to the one that works."""
        now = time.time()
        fresh = [(p, l) for p, l in self._providers
                 if self._cool.get(p, 0.0) <= now]
        resting = [(p, l) for p, l in self._providers
                   if self._cool.get(p, 0.0) > now]
        return fresh + resting

    def health(self) -> list[dict]:
        """Kesehatan tiap provider: siapa yang istirahat, sejak kapan, kenapa.

        Dibaca `model_status` supaya jawaban tentang model berasal dari
        KEADAAN, bukan dari konfigurasi. Inilah yang mencegah "aman" padahal
        429.
        """
        now = time.time()
        out: list[dict] = []
        for pid, llm in self._providers:
            rest = float(self._cool.get(pid, 0.0) or 0.0) - now
            out.append({
                "pid": pid,
                "model": str(getattr(llm, "model", "") or ""),
                "resting_s": round(rest, 1) if rest > 0 else 0.0,
                "error": str(self.last_error.get(pid, ""))[:160],
            })
        return out

    def complete(self, messages, tools=None, timeout: float = 120):
        errors = []
        for pid, llm in self._order():
            try:
                text, calls = llm.complete(messages, tools, timeout)
                self._cool.pop(pid, None)        # healthy again
                self.last_error.pop(pid, None)
                self.last = pid
                if errors:
                    # Say it out loud in the console: the user picked a model,
                    # a different one answered, and that must never look like
                    # the selection was ignored. The first failure's own words
                    # ride along, because "1 provider gagal" without a reason
                    # reads exactly like the picker being cosmetic.
                    print(f"[Brain] dijawab oleh '{pid}' (CADANGAN — "
                          f"{len(errors)} provider pilihan gagal: "
                          f"{str(errors[0])[:160]})")
                    fn = _fallback_hook
                    if fn is not None:
                        try:
                            fn(pid, str(getattr(llm, "model", "") or ""),
                               str(errors[0]))
                        except Exception:            # noqa: BLE001
                            pass
                return text, calls
            except Exception as e:                   # noqa: BLE001
                errors.append(f"{pid}: {e}")
                self.last_error[pid] = str(e)[:200]
                self._cool[pid] = time.time() + self.COOLDOWN
                print(f"[Brain] provider '{pid}' gagal — istirahat "
                      f"{int(self.COOLDOWN)}s, coba berikutnya "
                      f"({str(e)[:90]})")
        raise RuntimeError("; ".join(errors)[:300] or "no provider configured")


class CustomBrain:
    """One user's thinking, on their own provider, with the app's tools."""

    MAX_STEPS = 12     # agent-loop cap per turn — runaway protection
    HISTORY = 12       # exchanges kept in the rolling window
    RESULT_CAP = 4000  # a tool result longer than this is truncated
    LLM_TIMEOUT = 40   # seconds for one provider call — a voice turn that
                       # takes longer than this is a failure, not a wait

    def __init__(self, llm, system: str, max_steps: int | None = None):
        self.llm = llm
        self.system = system
        # Batas langkah bisa diatur dari config ("agent": {"max_steps": 12}).
        # 8 terlalu cepat untuk tugas nyata; 12 cukup untuk rantai panjang dan
        # masih jauh dari loop tak berujung.
        self.max_steps = int(max_steps or self._read_max_steps())
        self._hist: list[dict] = []
        # Tools discovered on demand (search_tools): attached to the next
        # request only, so the standing payload stays small while the whole
        # catalogue stays reachable.
        self._extra: dict[str, dict] = {}
        # Liveness signal for the caller's watchdog: touched at every step and
        # every tool result. A long project is allowed to take as long as it
        # needs while this keeps moving; a stuck provider stops moving it.
        self.last_progress = time.monotonic()
        # True selama satu panggilan provider sedang berjalan. Caller memakai
        # ini untuk membedakan "masih bekerja" dari "berhenti bergerak":
        # model besar bisa butuh >2 menit untuk satu langkah, dan memutusnya
        # di tengah jalan adalah bug, bukan perlindungan.
        self.in_call = False

    def reset(self) -> None:
        self._hist.clear()
        self._extra.clear()

    @staticmethod
    def _read_max_steps() -> int:
        """Batas langkah dari config, dengan batas aman 4..16."""
        try:
            import json
            from pathlib import Path
            p = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
            cfg = json.loads(p.read_text(encoding="utf-8"))
            v = int((cfg.get("agent") or {}).get("max_steps") or _HARD_STEP_CAP)
            return max(4, min(_HARD_STEP_CAP, v))
        except Exception:
            return _HARD_STEP_CAP

    def register_tools(self, decls) -> None:
        """Remember discovered tools for the next request (capped).

        Accepts either shape: a Gemini declaration ({"name": …}) or an
        OpenAI one ({"type": "function", "function": {"name": …}}) — the
        caller registers whatever the search returned.
        """
        for d in decls or []:
            if not isinstance(d, dict):
                continue
            name = d.get("name")
            if not name and isinstance(d.get("function"), dict):
                name = d["function"].get("name")
            if name:
                self._extra[str(name)] = d
        if len(self._extra) > 25:
            for k in list(self._extra)[:len(self._extra) - 25]:
                self._extra.pop(k, None)

    def clear_extra(self) -> None:
        """Drop tools discovered on demand, without touching the history.

        Fase 4 Slice 1: a turn classified as no-tool (greeting, farewell,
        dictation) must reach the provider with an EMPTY list — the
        on-demand registrations from earlier turns would otherwise ride
        along (turn() always appends _extra), and a greeting that can still
        call an MCP tool is a promise broken.
        """
        self._extra.clear()

    # ── the turn ────────────────────────────────────────────────────────────
    async def turn(self, utterance: str, tools, executor, context=None,
                   notes: str | None = None) -> str:
        """One user turn, start to finish.

        `tools` is the OpenAI-format tool list (providers.schema.to_openai).
        `executor` is an awaitable(name, args) -> str — the application's own
        tool dispatcher, so every existing gate still applies. `context` is
        the recent conversation (the exchanges that happened on the fast
        model too), which is what stops the brain from re-asking for details
        the user already gave. `notes` is an optional block of facts the
        application fetched on its own (Fase 4 Slice 4 — the user asked about
        something remembered); it rides inside the system prompt as
        [INGATAN_OTOMATIS], so it answers the question instead of becoming a
        new topic. Returns the sentence the voice should speak.
        """
        system = self.system
        if notes and str(notes).strip():
            system = (f"{system}\n\n{RECALL_NOTE_TAG}\n"
                      f"{str(notes).strip()}")
        msgs: list[dict] = [{"role": "system", "content": system}]
        if context:
            # A caller-supplied transcript is authoritative; the rolling
            # history would duplicate it.
            msgs.extend(context)
        else:
            msgs.extend(self._hist)
        msgs.append({"role": "user", "content": utterance})

        said = ""
        self.last_progress = time.monotonic()
        tool_cache: dict = {}
        tool_calls_made = 0
        for _ in range(self.max_steps):
            text, calls = "", []
            # One retry for transport noise (a dropped connection, a gateway
            # hiccup). Auth/quota failures are not retried: the same key
            # cannot start working twice in a row, and a retry only delays
            # the honest sentence the user needs to hear.
            for attempt in (1, 2):
                try:
                    self.in_call = True          # sedang bekerja, bukan mati
                    text, calls = await asyncio.to_thread(
                        self.llm.complete, msgs,
                        list(tools) + list(self._extra.values()),
                        self.LLM_TIMEOUT)
                    break
                except Exception as e:                   # noqa: BLE001
                    low = str(e).lower()
                    transient = any(k in low for k in (
                        "connection", "timed out", "timeout", "reset",
                        "temporarily", "unavailable", "502", "503", "504",
                        "empty"))
                    if attempt == 1 and transient:
                        await asyncio.sleep(0.8)
                        continue
                    said = f"Otak kustom tidak bisa dihubungi: {e}"
                    break
                finally:
                    self.in_call = False
            if said:
                break

            self.last_progress = time.monotonic()

            if not calls:
                said = (text or "").strip() or "…"
                break

            msgs.append({
                "role": "assistant",
                "content": text or "",
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name,
                                  "arguments": json.dumps(c.args or {},
                                                          ensure_ascii=False)}}
                    for c in calls
                ],
            })
            for c in calls:
                # 22 Okt — panggilan identik (tool+args) tidak dijalankan dua
                # kali dalam satu giliran: hasil pertama dikembalikan lagi
                # dengan catatan. Ini yang menghentikan loop 12 langkah.
                _key = (c.name, json.dumps(c.args or {}, sort_keys=True,
                                           ensure_ascii=False, default=str))
                if _key in tool_cache:
                    res = (tool_cache[_key]
                           + "\n\n[SUDAH DIPANGGIL — ini hasil yang sama; "
                             "jangan ulangi panggilan ini.]")
                else:
                    try:
                        res = await executor(c.name, c.args or {})
                    except Exception as e:               # noqa: BLE001
                        res = f"Tool error: {e}"
                    tool_cache[_key] = str(res)
                    tool_calls_made += 1
                # A completed tool is real progress: the watchdog may let a
                # long project keep going, and this is what it watches.
                self.last_progress = time.monotonic()
                # Koreksi diri: hasil yang jelas gagal diberi instruksi
                # eksplisit. Tanpa ini model cenderung mengulang panggilan yang
                # sama, atau lebih buruk, mengaku sudah berhasil.
                _full = str(res or "")
                _txt = _full[:self.RESULT_CAP]
                if len(_full) > self.RESULT_CAP:
                    # Silent truncation is what made the brain copy a file and
                    # split it into scratch parts: it never learned that the
                    # rest existed, only that it had "the" content.
                    _txt += (f"\n\n[DIPOTONG: ini {self.RESULT_CAP} karakter "
                             f"pertama dari {len(_full)}. JANGAN menyalin ke "
                             f"berkas bantu — minta sisanya dengan offset "
                             f"(file_controller read / code_helper read start+count).]")
                if _looks_failed(_txt):
                    _txt += ("\n\n[PERBAIKI] Langkah ini GAGAL. Jangan mengaku "
                             "berhasil. Periksa argumen, lalu coba pendekatan "
                             "atau tool lain. Kalau memang tidak mungkin, "
                             "katakan apa yang menghalangi.")
                msgs.append({
                    "role": "tool",
                    "tool_call_id": c.id,
                    "content": _txt,
                })
        else:
            said = (f"Saya berhenti setelah {self.max_steps} langkah dan tugas "
                    f"ini BELUM selesai. Sebutkan apa yang sudah dikerjakan, "
                    f"apa yang tersisa, dan tanyakan apakah saya lanjutkan.")

        # 22 Okt — klaim perubahan tanpa satu pun tool dijalankan adalah klaim
        # palsu (teramati di uji live: "sudah diganti" padahal 0 tool).
        if tool_calls_made == 0 and _CLAIM_RE.search(said or ""):
            said = ("Saya belum menjalankan tool apa pun, jadi belum ada yang "
                    "dikerjakan. Mau saya kerjakan sekarang?")

        # 22 Okt — sapaan dijawab sapaan, bukan "siap saya kerjakan".
        if (len((utterance or "").split()) <= 3
                and _GREET_RE.match(utterance or "")
                and _TASK_ACK_RE.search(said or "")):
            said = "Halo. Ada yang bisa saya bantu?"

        self._hist.append({"role": "user", "content": utterance})
        self._hist.append({"role": "assistant", "content": said})
        if len(self._hist) > 2 * self.HISTORY:
            del self._hist[:-2 * self.HISTORY]
        return said
