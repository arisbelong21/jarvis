"""
MCP server manager — discovers, boots, and dispatches external MCP tool
servers for JARVIS.

Config: config/mcp.json in the project root. Both shapes are accepted, so a
snippet copied from any MCP tutorial (Claude Code style "mcpServers") works
unchanged:

    {"servers":   {"rea": {"command": "cmd.exe", "args": ["/d","/c","npx","-y","rea-agents@6.5.0","mcp"]}}}
    {"mcpServers": {"rea": {"command": "npx",   "args": ["-y","rea-agents@6.5.0","mcp"]}}}

Rules that keep the voice session safe:

* An MCP server NEVER blocks boot. start_all() waits at most `deadline_s`;
  whatever is not ready by then initializes in the background and joins the
  tool list on the next session (Live tool declarations are fixed per session).
* A dead server is restarted transparently on the next call to its tool.
* One bad server cannot take the app down: every failure becomes a string the
  model reads, like any other tool result.
* Tool names are prefixed — mcp_<server>_<tool> — so REA's `analyze` can never
  shadow a JARVIS action of the same name, and the model always sees which
  server a tool belongs to.

Per-server keys:
    command        required — executable (on Windows use cmd.exe /d /c for .cmd shims)
    args           optional — argv after the command
    env            optional — extra environment variables
    enabled        optional — false keeps the server in the file but inactive
    startup_timeout_s  optional — handshake budget (default 60)
    call_timeout_s     optional — per-call budget (default 300)
    max_tools          optional — declare at most this many tools (0 = all).
                       A server with hundreds of tools costs real payload on
                       every session setup (REA's 207 = ~380 KB); this is the
                       dial for that, and the log says what it left out.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from core.mcp_client import MCPClient, MCPError, sanitize_tool_name
from core.mcp_schema import sanitize_tool_parameters

# A server id becomes part of every tool name it contributes (mcp_<id>_<tool>),
# so it has to survive the same character set Gemini accepts.
_SERVER_ID_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,31}$")


def _as_int(value, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(value))))
    except (TypeError, ValueError):
        return default


class MCPManager:
    def __init__(self, project_root: Path, logger=print, notify=None):
        self._root = Path(project_root)
        self._config_path = self._root / "config" / "mcp.json"
        self._log = logger
        self._notify = notify or (lambda msg: None)

        self._clients: dict[str, MCPClient] = {}
        self._records: dict[str, dict] = {}        # tool name -> record
        self._max_tools: dict[str, int] = {}       # server id -> declared cap
        self._boot_threads: list[threading.Thread] = []
        self._stop_lock = threading.Lock()

    # ── config ───────────────────────────────────────────────────────────────

    def load_config(self) -> dict:
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception as e:
            self._log(f"config/mcp.json unreadable ({e}) — no MCP servers")
            return {}
        servers = raw.get("servers") or raw.get("mcpServers") or {}
        return servers if isinstance(servers, dict) else {}

    # ── boot ─────────────────────────────────────────────────────────────────

    def start_all(self, deadline_s: float = 30.0) -> None:
        """Boot every enabled server in parallel; wait at most deadline_s.
        Servers that miss the deadline keep initializing in the background and
        their tools join on the next session — the app never waits on npx."""
        servers = {sid: cfg for sid, cfg in self.load_config().items()
                   if isinstance(cfg, dict) and cfg.get("enabled", True)
                   and cfg.get("command")}
        if not servers:
            self._log("no MCP servers configured (config/mcp.json)")
            return
        for sid, cfg in servers.items():
            client = MCPClient(
                name=sid,
                command=str(cfg["command"]),
                args=[str(a) for a in (cfg.get("args") or [])],
                env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
                cwd=str(cfg.get("cwd")) if cfg.get("cwd") else None,
                startup_timeout=float(cfg.get("startup_timeout_s", 60)),
                call_timeout=float(cfg.get("call_timeout_s", 300)),
                logger=lambda msg, sid=sid: self._log(f"{sid}: {msg}"),
            )
            self._clients[sid] = client
            try:
                self._max_tools[sid] = max(0, int(cfg.get("max_tools") or 0))
            except (TypeError, ValueError):
                self._max_tools[sid] = 0
            th = threading.Thread(target=self._boot_one, args=(sid, client),
                                  name=f"mcp-boot-{sid}", daemon=True)
            th.start()
            self._boot_threads.append(th)
        deadline = time.monotonic() + max(0.0, deadline_s)
        for th in self._boot_threads:
            th.join(timeout=max(0.0, deadline - time.monotonic()))
        ready = [sid for sid, c in self._clients.items() if c.alive()]
        if ready:
            total = len(self._records)
            line = f"MCP ready: {', '.join(ready)} ({total} tools)"
            self._log(line)
            self._notify(f"SYS: {line}")
        else:
            self._log("no MCP server finished initializing (they may join later)")

    def _boot_one(self, sid: str, client: MCPClient) -> None:
        try:
            client.ensure_up()
            self._register_tools(sid, client, self._max_tools.get(sid, 0))
        except Exception as e:
            tail = client.stderr_tail()
            self._log(f"{sid} failed to boot: {e}"
                      + (f" — {tail}" if tail else ""))

    def _register_tools(self, sid: str, client: MCPClient, max_tools: int = 0) -> None:
        try:
            tools = client.list_tools()
        except MCPError as e:
            self._log(f"{sid}: tools/list failed: {e}")
            return
        n = 0
        simplified = 0
        skipped = 0
        for tool in tools:
            if max_tools and n >= max_tools:
                skipped = len(tools) - n
                break
            orig = str(tool.get("name") or "").strip()
            if not orig:
                continue
            # MCP servers publish full JSON Schema; Gemini's function
            # declarations accept a much smaller subset. Translating here (see
            # core/mcp_schema.py) is what keeps a 207-tool server from failing
            # the whole session config with hundreds of validation errors.
            params, was_simplified = sanitize_tool_parameters(tool.get("inputSchema"))
            if was_simplified:
                simplified += 1
            name = sanitize_tool_name(sid, orig)
            if name in self._records:
                self._log(f"{sid}: duplicate tool '{orig}' — skipped")
                continue
            desc = (tool.get("description") or orig).strip()
            self._records[name] = {
                "name": name,
                "server": sid,
                "tool": orig,
                "description": f"[MCP server '{sid}'] {desc}",
                "parameters": params,
            }
            n += 1
        extra = f" ({simplified} schema(s) translated for Gemini)" if simplified else ""
        if skipped:
            extra += (f" — {skipped} not declared (max_tools={max_tools}); "
                      f"raise it in config/mcp.json to expose them")
        self._log(f"{sid}: {n} tools registered{extra}")

    # ── the action-registry surface main.py already knows ────────────────────

    def get_tool_declarations(self) -> list[dict]:
        return [{"name": r["name"], "description": r["description"],
                 "parameters": r["parameters"]} for r in self._records.values()]

    def has(self, name: str) -> bool:
        return name in self._records

    def scheduling(self, name: str):
        return None                                # MCP tools are plain blocking calls

    def run(self, name: str, args: dict) -> str:
        rec = self._records.get(name)
        if not rec:
            return f"Unknown MCP tool: {name}"
        client = self._clients.get(rec["server"])
        if client is None:
            return f"MCP server '{rec['server']}' is not running."
        try:
            client.ensure_up()                     # transparent restart if it died
            return client.call_tool(rec["tool"], args or {})
        except MCPError as e:
            tail = client.stderr_tail()
            msg = f"MCP tool '{rec['tool']}' on '{rec['server']}' failed: {e}"
            if tail:
                msg += f" — server said: {tail}"
            return msg
        except Exception as e:
            return f"MCP tool '{rec['tool']}' crashed: {e}"

    # ── introspection / shutdown ─────────────────────────────────────────────

    def status(self) -> list[dict]:
        return [{"server": sid,
                 "alive": c.alive(),
                 "tools": sum(1 for r in self._records.values() if r["server"] == sid)}
                for sid, c in self._clients.items()]

    # ── editing the server list from the UI ──────────────────────────────────
    # config/mcp.json stays the source of truth (a hand-edited file and the
    # panel are the same thing), but the panel no longer needs anyone to open
    # it: these four calls are what the CONNECTIONS screen uses. Every write
    # re-reads the file first, so a server someone added by hand between two
    # saves is never clobbered.

    def servers_for_ui(self) -> list[dict]:
        """One row per configured server, ready for the panel to draw."""
        out = []
        for sid, cfg in self.load_config().items():
            if not isinstance(cfg, dict):
                continue
            client = self._clients.get(sid)
            alive = bool(client and client.alive())
            tools = sum(1 for r in self._records.values() if r["server"] == sid)
            out.append({
                "id": sid,
                "enabled": bool(cfg.get("enabled", True)),
                "alive": alive,
                "tools": tools,
                "command": str(cfg.get("command") or ""),
                "args": list(cfg.get("args") or []),
                "env": dict(cfg.get("env") or {}),
                "cwd": str(cfg.get("cwd") or ""),
                "startup_timeout_s": cfg.get("startup_timeout_s", 60),
                "call_timeout_s": cfg.get("call_timeout_s", 300),
                "max_tools": cfg.get("max_tools", 0),
            })
        return out

    def _write_config(self, servers: dict) -> str:
        """Write the server map back, preserving everything else in the file."""
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
        except Exception:
            raw = {}
        raw.pop("mcpServers", None)            # one canonical shape after a save
        raw["servers"] = servers
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return ""
        except Exception as e:
            return f"could not write config/mcp.json ({e})"

    def save_server(self, sid: str, cfg: dict) -> str:
        """Add or replace one server. Returns '' on success, else a message."""
        sid = (sid or "").strip()
        if not sid or not _SERVER_ID_RE.match(sid):
            return ("The name must be letters, digits, dash or underscore "
                    "(e.g. 'my_tools').")
        if not isinstance(cfg, dict) or not str(cfg.get("command") or "").strip():
            return "A command is required — what should I run to start it?"
        clean = {
            "command": str(cfg["command"]).strip(),
            "args": [str(a) for a in (cfg.get("args") or [])],
            "enabled": bool(cfg.get("enabled", True)),
            "startup_timeout_s": _as_int(cfg.get("startup_timeout_s"), 60, 5, 900),
            "call_timeout_s": _as_int(cfg.get("call_timeout_s"), 300, 5, 3600),
            "max_tools": _as_int(cfg.get("max_tools"), 0, 0, 1000),
        }
        if cfg.get("env"):
            clean["env"] = {str(k): str(v) for k, v in dict(cfg["env"]).items()}
        if cfg.get("cwd"):
            clean["cwd"] = str(cfg["cwd"])

        with self._stop_lock:
            servers = {k: v for k, v in self.load_config().items() if isinstance(v, dict)}
            servers[sid] = clean
            err = self._write_config(servers)
        if err:
            return err
        self._log(f"{sid}: saved to config/mcp.json")
        return ""

    def delete_server(self, sid: str) -> str:
        with self._stop_lock:
            servers = {k: v for k, v in self.load_config().items() if isinstance(v, dict)}
            if sid not in servers:
                return f"'{sid}' is not configured."
            servers.pop(sid, None)
            err = self._write_config(servers)
            client = self._clients.pop(sid, None)
            self._max_tools.pop(sid, None)
        if client is not None:
            try:
                client.stop()
            except Exception:
                pass
        for name in [n for n, r in self._records.items() if r["server"] == sid]:
            self._records.pop(name, None)
        if err:
            return err
        self._log(f"{sid}: removed")
        return ""

    def restart_server(self, sid: str, deadline_s: float = 90.0) -> str:
        """Stop, start again, and report what came back — the TEST button."""
        with self._stop_lock:
            cfg = {k: v for k, v in self.load_config().items() if isinstance(v, dict)}.get(sid)
            old = self._clients.pop(sid, None)
            self._max_tools.pop(sid, 0)
        for name in [n for n, r in self._records.items() if r["server"] == sid]:
            self._records.pop(name, None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        if not isinstance(cfg, dict):
            return f"'{sid}' is not configured."

        client = MCPClient(
            name=sid,
            command=str(cfg.get("command") or ""),
            args=[str(a) for a in (cfg.get("args") or [])],
            env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
            cwd=str(cfg.get("cwd")) if cfg.get("cwd") else None,
            startup_timeout=float(cfg.get("startup_timeout_s", 60)),
            call_timeout=float(cfg.get("call_timeout_s", 300)),
            logger=lambda msg, sid=sid: self._log(f"{sid}: {msg}"),
        )
        self._clients[sid] = client
        self._max_tools[sid] = _as_int(cfg.get("max_tools"), 0, 0, 1000)
        th = threading.Thread(target=self._boot_one, args=(sid, client),
                              name=f"mcp-boot-{sid}", daemon=True)
        th.start()
        th.join(timeout=max(5.0, deadline_s))
        if not client.alive():
            tail = client.stderr_tail()
            return ("It did not come up. " + (tail or
                    "Check that the command exists and that Node/npx is on PATH."))
        tools = sum(1 for r in self._records.values() if r["server"] == sid)
        if tools == 0:
            return "It started but reported no tools."
        return f"Connected — {tools} tool(s) available."

    def stop_all(self) -> None:
        with self._stop_lock:
            for client in self._clients.values():
                try:
                    client.stop()
                except Exception:
                    pass
