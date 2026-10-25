"""
core/agent/runner.py — rencana → eksekusi → verifikasi → (ulang | lanjut).

Ini yang menjalankan pekerjaan sampai selesai, bukan sampai "terlihat selesai":

    untuk setiap langkah:
        izin?      deny → berhenti dengan alasan
                   confirm → PAUSE (state disimpan, bisa dilanjutkan)
        eksekusi   → hasil
        verifikasi → gagal? ulangi (maks N) → masih gagal? berhenti & laporkan
        berhasil   → catat, lanjut

Dua hal yang membuat ini tahan lama:
    • state ditulis ke memory/agent_state.json setiap langkah — jadi kalau
      aplikasi ditutup di tengah jalan, tugas bisa dilanjutkan, bukan diulang.
    • setiap aksi masuk audit trail (core/agent/audit.py), termasuk yang
      ditolak — jadi "apa yang kamu lakukan" selalu bisa dijawab.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.agent import audit
from core.agent.planner import make_plan, revise_step
from core.agent.planner import _default_llm as _plan_llm
from core.agent.verifier import verify as verify_step
from core.agent.verifier import _default_llm as _verify_llm

ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = ROOT / "memory" / "agent_state.json"

MAX_MODEL_CALLS = 24      # anggaran bawaan per tugas


class BudgetExceeded(BaseException):
    """Anggaran panggilan model untuk satu tugas habis.

    Sengaja turunan BaseException, bukan Exception: planner dan verifier
    menangkap Exception untuk memaafkan kegagalan model, dan sinyal anggaran
    tidak boleh ikut ditelan di sana — kalau tertelan, tugas berjalan terus
    melewati batas biaya. Ditangkap eksplisit di AgentRunner.run().
    """


class AgentRunner:
    def __init__(self, task: str, executor, tools: list[str], *,
                 config: dict | None = None, llm=None, verifier_llm=None,
                 max_steps: int = 10, max_retries: int = 2,
                 max_calls: int | None = None,
                 state_path: Path | None = None, on_progress=None):
        self.task = task
        self.executor = executor          # executor(tool, args) -> str
        self.tools = tools
        self.config = config or {}
        # Anggaran panggilan model: satu tugas berat bisa memanggil model
        # puluhan kali (rencana + verifikasi tiap langkah + revisi). Tanpa
        # batas, satu tugas yang salah rumus bisa menguras kuota tanpa
        # peringatan — jadi batasnya dihitung, bukan diharapkan.
        self.max_calls = int(max_calls or self._read_budget())
        self.calls = 0
        self.llm = llm or _plan_llm
        self.verifier_llm = verifier_llm or _verify_llm
        self.max_steps = max_steps
        self.max_retries = max(0, max_retries)
        self.state_path = Path(state_path or STATE_PATH)
        self.on_progress = on_progress

    @staticmethod
    def _read_budget() -> int:
        try:
            cfg = json.loads((ROOT / "config" / "api_keys.json")
                             .read_text(encoding="utf-8"))
            v = int((cfg.get("agent") or {}).get("max_model_calls")
                    or MAX_MODEL_CALLS)
            return max(4, min(120, v))
        except Exception:
            return MAX_MODEL_CALLS

    def _counted(self, fn):
        """Bungkus fungsi model supaya setiap panggilan tercatat & dibatasi."""
        def inner(prompt):
            if self.calls >= self.max_calls:
                raise BudgetExceeded(
                    f"anggaran {self.max_calls} panggilan model habis")
            self.calls += 1
            return fn(prompt)
        return inner

    # ── state ────────────────────────────────────────────────────────────

    def _save(self, state: dict) -> None:
        try:
            state["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:                       # noqa: BLE001
            print(f"[Agent] gagal menyimpan state: {e}")

    def _clear(self) -> None:
        try:
            self.state_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _say(self, text: str) -> None:
        print(f"[Agent] {text}")
        if callable(self.on_progress):
            try:
                self.on_progress(text)
            except Exception:
                pass

    # ── jalan ────────────────────────────────────────────────────────────

    def run(self, state: dict | None = None) -> dict:
        if state is None:
            self._say("menyusun rencana…")
            try:
                plan = make_plan(self.task, self.tools,
                                 llm=self._counted(self.llm),
                                 max_steps=self.max_steps)
            except BudgetExceeded as e:
                self._say(f"anggaran model habis saat merencanakan: {e}")
                return {"status": "failed", "goal": self.task,
                        "steps_total": 0, "steps_done": 0, "unverified": 0,
                        "error": f"anggaran model habis saat merencanakan",
                        "pending": None, "transcript": [],
                        "model_calls": self.calls}
            except Exception as e:                   # noqa: BLE001
                # Perencanaan gagal bukan alasan untuk melempar exception ke
                # thread pekerjaan: laporkan sebagai kegagalan yang bisa dibaca.
                self._say(f"gagal menyusun rencana: {e}")
                return {"status": "failed", "goal": self.task,
                        "steps_total": 0, "steps_done": 0, "unverified": 0,
                        "error": f"rencana gagal: {e}", "pending": None,
                        "transcript": []}
            state = {"task": self.task, "plan": plan, "index": 0,
                     "status": "running", "transcript": [],
                     "created": time.strftime("%Y-%m-%d %H:%M:%S")}
            self._save(state)
            self._say(f"rencana: {len(plan['steps'])} langkah — {plan['goal']}")

        steps = state["plan"]["steps"]
        i = int(state.get("index", 0))

        while i < len(steps):
            step = steps[i]
            tool = step.get("tool") or ""
            args = step.get("args") or {}

            # Pintu keputusan pusat — aturan sama dengan jalur suara/Telegram.
            from core import decision_loop as _decision
            decision, why = _decision.gate(tool, args, config=self.config,
                                           known=True)
            if decision == "deny":
                audit.record(tool, args, actor="agent", decision="deny",
                             ok=False, summary=why)
                state["status"] = "failed"
                state["error"] = f"langkah {step['id']} diblokir: {why}"
                self._save(state)
                self._say(state["error"])
                return self._result(state)

            if decision == "confirm":
                state["status"] = "paused"
                state["pending"] = {"step": step, "why": why}
                self._save(state)
                self._say(f"berhenti menunggu izin: {step['title']} ({why})")
                return self._result(state)

            self._say(f"langkah {step['id']}/{len(steps)}: {step['title']}")
            try:
                outcome = self._run_step(step)
            except BudgetExceeded as e:
                state["status"] = "budget"
                state["error"] = (f"anggaran model habis di langkah "
                                  f"{step['id']} ({e}) — {self.calls} panggilan")
                self._save(state)
                self._say(state["error"])
                return self._result(state)

            entry = {"id": step["id"], "title": step["title"], "tool": tool,
                     "ok": outcome["ok"], "reason": outcome["reason"],
                     "unverified": outcome.get("unverified", False),
                     "ms": outcome["ms"], "tries": outcome["tries"]}
            state["transcript"].append(entry)
            state["index"] = i + 1
            i += 1

            if not outcome["ok"]:
                state["status"] = "failed"
                state["error"] = (f"langkah {step['id']} gagal: "
                                  f"{outcome['reason']}")
                self._save(state)
                self._say(state["error"])
                return self._result(state)

            self._save(state)

        state["status"] = "done"
        self._save(state)
        self._say("selesai")
        result = self._result(state)
        self._clear()          # tugas tuntas → tidak perlu dilanjutkan lagi
        return result

    def _run_step(self, step: dict) -> dict:
        """Jalankan satu langkah; kalau gagal, REVISI langkahnya lalu coba lagi.

        Percobaan pertama memakai langkah asli. Percobaan berikutnya memakai
        langkah hasil revisi (tool/argumen berbeda) — bukan pengulangan yang
        sama, karena mengulang hal yang sama hampir selalu gagal dengan cara
        yang sama."""
        tries = 0
        last = {"ok": False, "reason": "belum dijalankan", "unverified": False}
        current = dict(step)
        while tries <= self.max_retries:
            tries += 1
            tool = current.get("tool") or ""
            args = current.get("args") or {}
            t0 = time.monotonic()
            try:
                result = str(self.executor(tool, args) or "")
            except Exception as e:                   # noqa: BLE001
                result = f"ERROR: {type(e).__name__}: {e}"
            ms = int((time.monotonic() - t0) * 1000)

            verdict = verify_step(current, result,
                                  llm=self._counted(self.verifier_llm))
            audit.record(tool, args, actor="agent",
                         decision="allow", ok=verdict["ok"], ms=ms,
                         summary=verdict.get("reason", ""))
            last = {"ok": verdict["ok"], "reason": verdict.get("reason", ""),
                    "unverified": verdict.get("unverified", False),
                    "ms": ms, "tries": tries}
            if verdict["ok"]:
                return last
            if tries > self.max_retries:
                break

            # Revisi langkah memakai saran verifier. Kalau model menyerah,
            # berhenti di sini — mengulang tanpa perubahan tidak berguna.
            revised = revise_step(current, result, verdict.get("fix", ""),
                                  self.tools, llm=self._counted(self.llm))
            if not revised:
                self._say(f"  ✖ tidak ada cara lain: {verdict.get('reason','')}")
                break
            current = revised
            self._say(f"  ↻ revisi ({tries}/{self.max_retries}): "
                      f"{current.get('tool')} — "
                      f"{verdict.get('fix') or verdict.get('reason')}")
            time.sleep(0.3)
        return last

    def _result(self, state: dict) -> dict:
        steps = state["plan"]["steps"]
        done = [t for t in state["transcript"] if t.get("ok")]
        unverified = [t for t in done if t.get("unverified")]
        return {
            "status": state.get("status"),
            "goal": state["plan"].get("goal", ""),
            "steps_total": len(steps),
            "steps_done": len(done),
            "unverified": len(unverified),
            "error": state.get("error", ""),
            "pending": state.get("pending"),
            "transcript": state["transcript"],
            "model_calls": self.calls,
        }


# ── antrean persisten ────────────────────────────────────────────────────────

def load_pending(path: Path | None = None) -> dict | None:
    """Tugas yang belum selesai saat aplikasi ditutup, atau None."""
    p = Path(path or STATE_PATH)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # "budget" ikut bisa dilanjutkan: pekerjaannya belum selesai, hanya
        # kehabisan jatah panggilan model.
        if data.get("status") in ("running", "paused", "budget"):
            return data
    except Exception:
        pass
    return None


def clear_pending(path: Path | None = None) -> None:
    try:
        Path(path or STATE_PATH).unlink(missing_ok=True)
    except Exception:
        pass
