"""sonde_rea_mcp.py — bisa tidak server MCP REA dijalankan dari mesin ini?

Uji NYATA (bukan cuma baca config): spawn `npx -y rea-agents@6.5.0 mcp`
persis seperti config/mcp.json proyek (cmd /d /c npx ...), lalu handshake
JSON-RPC newline-delimited di stdio (pola core/mcp_client.py):

  1. initialize            -> serverInfo + protocolVersion
  2. notifications/initialized
  3. tools/list            -> hitung tool + contoh nama
  4. ping (opsional)       -> bukti server benar-benar melayani

Env (JAVA_HOME, GHIDRA_INSTALL_DIR, ...) diambil dari config/mcp.json.
Keluarannya: check + "ERRORS: none" kalau semua lolos.

Jalankan dari root proyek:  python _refwork/sonde_rea_mcp.py
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "mcp.json")

ERRORS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print(("  OK   " if ok else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        ERRORS.append(name + (f" ({detail})" if detail else ""))


def main():
    with open(CFG, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    srv = (cfg.get("servers") or cfg.get("mcpServers") or {}).get("rea")
    check("config/mcp.json punya server 'rea'", bool(srv))
    if not srv:
        return
    check("server 'rea' enabled", srv.get("enabled") is True)

    env = dict(os.environ)
    env.update(srv.get("env") or {})

    print("· spawn:", " ".join([srv.get("command", "")] + list(srv.get("args") or [])))
    t0 = time.time()
    proc = subprocess.Popen(
        [srv.get("command")] + list(srv.get("args") or []),
        cwd=ROOT, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    check("proses MCP hidup", proc.poll() is None, f"pid={proc.pid}")

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def read(timeout):
        """Baca 1 baris JSON dari stdout-server; None kalau timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if line:
                line = line.strip()
                if line:
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        print("  (bukan JSON):", line[:120])
                        continue
            if proc.poll() is not None:
                return None
        return None

    # --- 1. initialize -------------------------------------------------
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "sonde-rea-mcp", "version": "1.0"},
    }})
    rsp = read(srv.get("startup_timeout_s", 90))
    ok = bool(rsp and rsp.get("result"))
    check("initialize dijawab", ok, "" if not ok else str((rsp["result"].get("serverInfo") or {})))
    if not ok:
        err = proc.stderr.read() if proc.stderr else ""
        print("  stderr:", (err or "")[:400])
        proc.kill()
        return
    ver = rsp["result"].get("protocolVersion")
    check("protocolVersion dikirim server", bool(ver), str(ver))

    # --- 2. initialized -------------------------------------------------
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # --- 3. tools/list ---------------------------------------------------
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    rsp = read(30)
    tools = ((rsp or {}).get("result") or {}).get("tools") or []
    check("tools/list dijawab", bool(tools) and "error" not in (rsp or {}),
          f"{len(tools)} tool")
    if tools:
        names = [t.get("name", "?") for t in tools]
        print("  contoh 12 tool pertama:")
        for n in names[:12]:
            print("    -", n)
        rea = [n for n in names if n.startswith("mcp__rea")]
        print(f"  bernama 'mcp__rea*': {len(rea)}")
        # kategori kasar biar kelihatan cakupan REA
        cats = {}
        for n in names:
            head = n.split("_")[2] if n.startswith("mcp__rea_") and len(n.split("_")) > 2 else n.split("_")[0]
            cats[head] = cats.get(head, 0) + 1
        top = sorted(cats.items(), key=lambda kv: -kv[1])[:12]
        print("  kategori teratas:", ", ".join(f"{k}={v}" for k, v in top))
        check(">= 50 tool tersedia (REA lengkap)", len(names) >= 50, str(len(names)))

    # --- 4. ping (opsional; bukan bagian wajib MCP) ----------------------
    try:
        send({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}})
        rsp = read(15)
        check("ping dijawab (server melayani)", bool(rsp and "result" in rsp))
    except Exception as exc:  # ping opsional — jangan gagalkan sonde
        check("ping dijawab (server melayani)", True, f"dilewati: {exc}")

    dt = time.time() - t0
    print(f"· handshake selesai dalam {dt:.1f}s")

    try:
        proc.stdin.close()
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    err = ""
    try:
        err = proc.stderr.read() if proc.stderr else ""
    except Exception:
        pass
    if err.strip():
        print("  stderr server (300 char):", err.strip()[:300])

    print(f"\n{CHECKS[0]} check dijalankan")
    print("ERRORS:", "none" if not ERRORS else ERRORS)


if __name__ == "__main__":
    sys.exit(main())
