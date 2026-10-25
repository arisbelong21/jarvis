"""
Minimal MCP (Model Context Protocol) client over stdio — zero dependencies.

JARVIS speaks to external tool servers (REA and anything else that ships an
MCP server) the same way Claude Code or Cursor does: the server runs as a
child process, and JSON-RPC 2.0 messages are exchanged one-per-line over its
stdin/stdout. This module implements exactly the slice JARVIS needs —

    initialize  →  notifications/initialized  →  tools/list  →  tools/call

— with thread-safe writes, a reader thread that fills pending results, a
watchdog on dead processes, version negotiation on the handshake, and a clean
stop(). Anything fancier (sampling, roots, progress) is deliberately absent:
JARVIS neither offers nor needs it.

Threading model: a call may come from any thread (main.py dispatches tool
calls through run_in_executor). Writes are serialized by a lock, responses are
routed by JSON-RPC id through an event + result slot, and the reader owns the
process' stdout so nothing else ever touches it.

Windows note: MCP servers are usually launched through `cmd.exe /d /c npx ...`
(the npx shim is a .cmd, which CreateProcess cannot exec directly) — that is
how the REA config in config/mcp.json is written.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

# Newest first; a server that rejects one gets the next. Both are real,
# published MCP revisions — nothing invented.
_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")

_CLIENT_INFO = {"name": "jarvis-mcp-client", "version": "1.0"}

_MAX_LIST_PAGES = 5          # tools/list pagination guard

# One tool call must not flood the voice session — but a blunt 8 KB cut lands
# in the middle of real evidence. Measured against REA: a JavaScript/Electron
# summary of a mid-size app.asar is ~11.6 KB, so the old cap handed the model a
# broken JSON payload. 40 KB keeps every evidence view we measured whole with
# margin to spare.
_MAX_OUTPUT_CHARS = 40000

# Bulk inventories are a different problem: a full REA procedure list for
# notepad.exe is ~205 KB and `list_names` is ~1.4 MB — no voice session can hold
# that, and silently dropping it loses the answer. So above the cap the complete
# bytes go to logs/mcp_overflow/ (already covered by .gitignore) and the model
# gets the path, a readable head, and a nudge to narrow the query. JARVIS can
# read the file back with file_controller · read_file.
_OVERFLOW_INLINE_CHARS = 4000
_OVERFLOW_KEEP = 20
_OVERFLOW_DIR = Path(__file__).resolve().parent.parent / "logs" / "mcp_overflow"
_DEAD_PROCESS_GRACE = 3.0    # poll() grace after apparent death (Windows)


class MCPError(Exception):
    """Raised for transport failures, timeouts, and JSON-RPC error responses."""


def sanitize_tool_name(server: str, tool: str) -> str:
    """Gemini Live tool names are [a-zA-Z0-9_]{1,64}; MCP tool names may
    contain hyphens (analyze-javascript-application). Keep a deterministic,
    collision-free mapping back to the original name via _unsanitize."""
    raw = f"mcp_{server}_{tool}".lower()
    cleaned = "".join(c if (c.isalnum() or c == "_") else "_" for c in raw)
    return cleaned[:64] if len(cleaned) > 64 else cleaned


class MCPClient:
    """One MCP server process. Not reentrant per call — one call at a time is
    the normal pattern; concurrent calls simply queue on the write lock."""

    def __init__(self, name: str, command: str, args: list | None = None,
                 env: dict | None = None, cwd: str | None = None,
                 startup_timeout: float = 45.0, call_timeout: float = 300.0,
                 logger=print):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env_extra = dict(env or {})
        self.cwd = cwd
        self.startup_timeout = float(startup_timeout)
        self.call_timeout = float(call_timeout)
        self._log = logger

        self._proc: subprocess.Popen | None = None
        self._write_lock = threading.Lock()
        self._io_lock = threading.Lock()          # serializes start/stop/call
        self._pending: dict[int, dict] = {}       # id -> {"event", "result", "error"}
        self._next_id = 0
        self._reader: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._stopped = False

    # ── process lifecycle ────────────────────────────────────────────────────

    @property
    def _cmdline(self) -> list[str]:
        return [self.command] + self.args

    def _spawn(self) -> None:
        env = dict(os.environ)
        env.update(self.env_extra)
        self._proc = subprocess.Popen(
            self._cmdline,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd or None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,          # line buffered — messages are newline-delimited
        )
        self._stopped = False
        self._reader = threading.Thread(target=self._read_stdout,
                                        name=f"mcp-{self.name}-reader", daemon=True)
        self._reader.start()
        threading.Thread(target=self._drain_stderr,
                         name=f"mcp-{self.name}-stderr", daemon=True).start()

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue                       # server chatter, not JSON
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                if mid is not None and "method" not in msg:
                    # a response to one of our requests
                    slot = self._pending.get(mid)
                    if slot is not None:
                        if "error" in msg:
                            slot["error"] = msg["error"]
                        else:
                            slot["result"] = msg.get("result")
                        slot["event"].set()
                elif mid is not None and "method" in msg:
                    # a server-initiated request (ping). Answer, never block.
                    self._write({"jsonrpc": "2.0", "id": mid,
                                 "error": {"code": -32601,
                                           "message": f"method not supported: {msg['method']}"}})
                # notifications (no id, no response expected) are ignored
        except Exception:
            pass                                  # pipe closed — death handled elsewhere
        finally:
            # Wake every waiter: with the pipe gone, no answer is coming.
            for slot in list(self._pending.values()):
                if slot["error"] is None and slot["result"] is None:
                    slot["error"] = {"code": -32000,
                                     "message": "MCP server closed the connection"}
                slot["event"].set()

    def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for line in self._proc.stderr:
                with self._stderr_lock:
                    self._stderr_lines.append(line.rstrip())
                    if len(self._stderr_lines) > 50:
                        self._stderr_lines.pop(0)
        except Exception:
            pass

    def _write(self, obj: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        data = json.dumps(obj, ensure_ascii=False) + "\n"
        with self._write_lock:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()

    def _request(self, method: str, params: dict | None = None,
                 timeout: float = 30.0) -> dict:
        """One JSON-RPC round trip. Raises MCPError on timeout / dead process /
        error response."""
        if self._proc is None or self._proc.poll() is not None:
            raise MCPError("MCP server process is not running")
        with self._write_lock:
            self._next_id += 1
            mid = self._next_id
            slot = {"event": threading.Event(), "result": None, "error": None}
            self._pending[mid] = slot
        try:
            self._write({"jsonrpc": "2.0", "id": mid, "method": method,
                         **({"params": params} if params is not None else {})})
        except Exception as e:
            self._pending.pop(mid, None)
            raise MCPError(f"could not send '{method}': {e}")
        if not slot["event"].wait(timeout):
            self._pending.pop(mid, None)
            raise MCPError(f"'{method}' timed out after {timeout:.0f}s")
        self._pending.pop(mid, None)
        if slot["error"] is not None:
            raise MCPError(f"{method} → error {slot['error'].get('code')}: "
                           f"{slot['error'].get('message')}")
        return slot["result"] or {}

    def _notify(self, method: str, params: dict | None = None) -> None:
        obj = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            obj["params"] = params
        try:
            self._write(obj)
        except Exception:
            pass                                  # best-effort: server may have died

    # ── the four things JARVIS actually does ─────────────────────────────────

    def initialize(self) -> None:
        """Handshake with protocol-version negotiation."""
        last: Exception | None = None
        for version in _PROTOCOL_VERSIONS:
            try:
                self._request("initialize", {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                }, timeout=self.startup_timeout)
                self._notify("notifications/initialized")
                self._log(f"connected (protocol {version})")
                return
            except MCPError as e:
                last = e
                if "version" in str(e).lower() or "-32602" in str(e):
                    continue                       # that revision not accepted
                raise
        raise MCPError(f"handshake failed: {last}")

    def list_tools(self) -> list[dict]:
        """Every tool the server advertises (paginated)."""
        tools: list[dict] = []
        cursor = None
        for _ in range(_MAX_LIST_PAGES):
            params = {"cursor": cursor} if cursor else None
            res = self._request("tools/list", params,
                                timeout=min(self.startup_timeout, 60.0))
            tools.extend(res.get("tools") or [])
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call one tool and flatten its content blocks to plain text."""
        res = self._request("tools/call",
                            {"name": tool_name, "arguments": arguments or {}},
                            timeout=self.call_timeout)
        return self._flatten(res, tool_name)

    def _spill(self, tool_name: str, text: str) -> str | None:
        """Save an oversized result to logs/mcp_overflow/; return its path.

        Never raises: a payload too big for the session is still worth more on
        disk than thrown away, but a failure here must not break the call."""
        try:
            _OVERFLOW_DIR.mkdir(parents=True, exist_ok=True)
            safe = "".join(c if (c.isalnum() or c in "._-") else "_"
                           for c in tool_name)[:60] or "tool"
            path = _OVERFLOW_DIR / f"{self.name}_{safe}_{time.strftime('%Y%m%d-%H%M%S')}.txt"
            path.write_text(text, encoding="utf-8")
            # Evidence dumps are megabytes each — keep only the newest few.
            files = sorted(_OVERFLOW_DIR.glob("*.txt"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[_OVERFLOW_KEEP:]:
                try:
                    old.unlink()
                except OSError:
                    pass
            return str(path)
        except Exception:
            return None

    def _flatten(self, res: dict, tool_name: str = "") -> str:
        parts: list[str] = []
        for block in res.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        structured = res.get("structuredContent")
        if structured and not parts:
            try:
                parts.append(json.dumps(structured, ensure_ascii=False, indent=2))
            except Exception:
                pass
        if not parts:
            parts.append("(tool returned no content)")
        text = "\n".join(parts)
        if res.get("isError"):
            text = f"[MCP tool error] {text}"
        if len(text) > _MAX_OUTPUT_CHARS:
            total = len(text)
            spilled = self._spill(tool_name, text)
            head = text[:_OVERFLOW_INLINE_CHARS]
            if spilled:
                text = (f"[OUTPUT TOO LARGE — {total} characters. The complete payload "
                        f"is saved at:\n{spilled}\n"
                        f"Read it back with file_controller (action read_file, raise "
                        f"max_chars as needed), or ask the server for a narrower result "
                        f"instead of trusting the head below as the whole answer. "
                        f"First {_OVERFLOW_INLINE_CHARS} characters:\n\n{head}")
            else:
                text = (text[:_MAX_OUTPUT_CHARS]
                        + f"\n\n[INCOMPLETE — cut at {_MAX_OUTPUT_CHARS} of "
                          f"{total} characters, and it could not be saved to disk. "
                          f"This is not the whole answer: ask again with a smaller "
                          f"scope (for REA use detail=\"summary\", a narrower query, "
                          f"or a view/page call).]")
        return text

    # ── health / restart / stop ──────────────────────────────────────────────

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure_up(self) -> None:
        """Start or restart the server if it is not running. Serialized so two
        threads racing a dead server cannot spawn two processes."""
        with self._io_lock:
            if self.alive():
                return
            self._teardown_process()
            self._spawn()
            self.initialize()

    def _teardown_process(self) -> None:
        proc, self._proc = self._proc, None
        self._pending.clear()
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        except Exception:
            pass

    def stderr_tail(self, n: int = 5) -> str:
        with self._stderr_lock:
            return " | ".join(self._stderr_lines[-n:])

    def stop(self) -> None:
        with self._io_lock:
            if self._stopped:
                return
            self._stopped = True
            self._teardown_process()
