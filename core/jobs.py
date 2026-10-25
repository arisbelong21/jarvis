"""
core/jobs.py — a small background queue for heavy work.

WHY IT EXISTS
    A brain turn runs inside the Live session's receive loop, so a long job
    (build a project, analyse a big file) lives or dies with that session.
    When Gemini's session hits its duration limit and reconnects, the turn is
    cancelled and the work is lost — and while it runs, the assistant cannot
    answer anything else.

    A job here runs on its own thread with its own lifetime. A reconnect
    cannot touch it, the microphone stays free, and the result is handed back
    through `on_done` so the assistant can say it when it is ready.

WHAT IT DELIBERATELY IS NOT
    It is not a scheduler: one worker, first-in-first-out, no priorities, no
    persistence across app restarts. Those are features with their own
    failure modes; this exists to survive a reconnect, and nothing more.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Job:
    id: str
    title: str
    fn: object                 # callable() -> str
    status: str = "queued"     # queued | running | done | error
    result: str = ""
    error: str = ""
    step: str = ""             # what the job is doing right now, if it says
    started: float = 0.0
    finished: float = 0.0
    tool: str = ""             # which tool this job runs ("composio_actions", …)

    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        end = self.finished or time.monotonic()
        return end - self.started


class JobRunner:
    """One worker thread, a FIFO queue, and a callback when a job ends."""

    KEEP = 20                  # finished jobs kept for status/audit

    def __init__(self, on_done=None, on_start=None, on_progress=None,
                 name: str = "jarvis-jobs", persist_path: Path | str | None = None):
        self._q: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._on_done = on_done
        self._on_start = on_start
        self._on_progress = on_progress
        # Deskripsi job yang bisa ditulis ke berkas. Callable tidak bisa
        # diserialisasi, jadi yang disimpan adalah RESEP-nya (tool + argumen);
        # resep itulah yang dikirim ulang setelah aplikasi dibuka lagi.
        self._desc: dict[str, dict] = {}
        self._persist_path = Path(persist_path) if persist_path else None
        self._thread = threading.Thread(target=self._loop, name=name,
                                        daemon=True)
        self._thread.start()

    # ── persistence ────────────────────────────────────────────────────────

    def _persist(self) -> None:
        if not self._persist_path:
            return
        try:
            with self._lock:
                items = [{"id": j.id, "title": j.title, "status": j.status,
                          "descriptor": self._desc[j.id]}
                         for j in self._jobs.values()
                         if j.id in self._desc
                         and j.status in ("queued", "running")]
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps({"jobs": items}, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as e:                       # noqa: BLE001
            print(f"[Jobs] gagal menyimpan antrean: {e}")

    def pending(self) -> list[dict]:
        """Job yang belum selesai saat aplikasi terakhir ditutup."""
        if not self._persist_path:
            return []
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            return [j for j in (data.get("jobs") or [])
                    if isinstance(j, dict) and j.get("descriptor")]
        except Exception:
            return []

    def forget_pending(self) -> None:
        try:
            if self._persist_path:
                self._persist_path.unlink(missing_ok=True)
        except Exception:
            pass

    # ── public API ──────────────────────────────────────────────────────────

    def submit(self, title: str, fn, descriptor: dict | None = None) -> str:
        """Queue a callable and return its job id immediately.

        `descriptor` (opsional) adalah resep yang bisa ditulis ke berkas —
        {"tool": …, "args": …} — supaya pekerjaan ini bisa ditawarkan lagi
        setelah aplikasi ditutup dan dibuka kembali.
        """
        with self._lock:
            self._seq += 1
            jid = f"job{self._seq}"
            self._jobs[jid] = Job(id=jid, title=str(title or "pekerjaan"),
                                  fn=fn,
                                  tool=str((descriptor or {}).get("tool") or ""))
            if descriptor:
                self._desc[jid] = dict(descriptor)
            self._prune_locked()
        self._q.put(jid)
        self._persist()
        return jid

    def busy(self) -> bool:
        """True while anything is queued or running."""
        with self._lock:
            return any(j.status in ("queued", "running")
                       for j in self._jobs.values())

    def current(self) -> Job | None:
        """The job running right now, or None."""
        with self._lock:
            for j in self._jobs.values():
                if j.status == "running":
                    return j
        return None

    def set_step(self, text: str) -> None:
        """Record what the running job is doing — shown live in the HUD."""
        with self._lock:
            for j in self._jobs.values():
                if j.status == "running":
                    j.step = str(text or "")[:80]
                    break

    def status(self) -> list[dict]:
        with self._lock:
            return [{
                "id": j.id, "title": j.title, "status": j.status,
                "elapsed": round(j.elapsed(), 1), "step": j.step,
                "result": j.result[:300], "error": j.error,
            } for j in self._jobs.values()]

    def progress(self, text: str) -> None:
        """Let a running job report a line to the interface."""
        if self._on_progress:
            try:
                self._on_progress(str(text or ""))
            except Exception:
                pass

    # ── worker ──────────────────────────────────────────────────────────────

    def _prune_locked(self) -> None:
        done = [j for j in self._jobs.values()
                if j.status in ("done", "error")]
        if len(done) > self.KEEP:
            done.sort(key=lambda j: j.finished)
            for j in done[:len(done) - self.KEEP]:
                self._jobs.pop(j.id, None)

    def _loop(self) -> None:
        while True:
            jid = self._q.get()
            with self._lock:
                job = self._jobs.get(jid)
            if job is None:
                continue
            job.status = "running"
            job.started = time.monotonic()
            self._persist()
            if self._on_start:
                try:
                    self._on_start(job.title)
                except Exception:
                    pass
            try:
                job.result = str(job.fn() or "")
                job.status = "done"
            except Exception as e:                     # noqa: BLE001
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}"
                print(f"[Jobs] '{job.title}' gagal: {job.error}")
                traceback.print_exc()
            job.finished = time.monotonic()
            # Selesai (berhasil atau gagal) → resepnya tidak perlu ditawarkan
            # lagi setelah restart.
            with self._lock:
                self._desc.pop(jid, None)
            self._persist()
            print(f"[Jobs] '{job.title}' {job.status} dalam "
                  f"{job.elapsed():.1f}s")
            if self._on_done:
                try:
                    self._on_done(job)
                except Exception as e:                 # noqa: BLE001
                    print(f"[Jobs] callback on_done gagal: {e}")
