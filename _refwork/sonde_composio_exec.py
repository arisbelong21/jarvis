# -*- coding: utf-8 -*-
"""Execution-layer probe for the composio_actions plugin (offline — no network).

Every HTTP call is replaced at the single seam (`_urlopen`), and the plugin's
config is replaced at `_config`, so this exercises the real request/response
paths against canned Composio payloads: schema-driven argument mapping,
execute envelope unwrapping, drift hints, connection pre-checks, discover,
run_tool verbatim passthrough, and voice-friendly rendering.
"""
import importlib.util
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = []
ERRORS = []
CALLS = []          # (method, url, body) of every request the plugin made


def check(name, cond, detail=""):
    line = f"  [{'OK' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else "")
    REPORT.append(line)
    if not cond:
        ERRORS.append(name)


class FakeResp:
    def __init__(self, payload, status=200):
        self._data = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _history_read(call) -> bool:
    """GrokBot 22 Okt: pembacaan riwayat TERKIRIM (GMAIL_FETCH_EMAILS
    query in:sent) untuk cek penerima adalah BACA, bukan kirim — tidak
    dihitung sebagai eksekusi yang dijaga gerbang."""
    body = call[2] or {}
    return ("GMAIL_FETCH_EMAILS" in call[1]
            and (body.get("arguments") or {}).get("query") == "in:sent")


def make_urlopen(scenario):
    """scenario: dict(url substring) -> callable(method, url, body) -> (payload, status)"""
    def fake(req, timeout):
        method, url = req.method, req.full_url
        body = json.loads(req.data.decode()) if req.data else None
        CALLS.append((method, url, body))
        for key, fn in scenario.items():
            if key in url:
                payload, status = fn(method, url, body)
                if status >= 400:
                    raise __import__("urllib.error", fromlist=["error"]).HTTPError(
                        url, status, "err", None,
                        io.BytesIO(json.dumps(payload).encode()))
                return FakeResp(payload)
        raise AssertionError(f"unexpected request: {method} {url}")
    return fake


SCHEMAS = {
    "/tools/GMAIL_SEND_EMAIL": {"input_parameters": {
        "recipient_email": {"type": "string", "required": True},
        "subject": {"type": "string", "required": True},
        "body": {"type": "string", "required": True},
        "is_html": {"type": "boolean", "required": False},
        "attachment": {"type": "string", "required": False}}},
    "/tools/GMAIL_CREATE_EMAIL_DRAFT": {"input_parameters": {
        "recipient": {"type": "string", "required": True},
        "subject": {"type": "string", "required": True},
        "body": {"type": "string", "required": True}}},
    "/tools/GMAIL_FETCH_EMAILS": {"input_parameters": {
        "query": {"type": "string", "required": False},
        "max_results": {"type": "number", "required": False}}},
    "/tools/GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID": {"input_parameters": {
        "message_id": {"type": "string", "required": True}}},
    "/tools/GMAIL_DELETE_MESSAGE": {"input_parameters": {
        "message_id": {"type": "string", "required": True}}},
    "/tools/GOOGLECALENDAR_EVENTS_LIST": {"input_parameters": {
        "calendar_id": {"type": "string", "required": False},
        "time_min": {"type": "string", "required": False},
        "max_results": {"type": "number", "required": False}}},
    "/tools/GOOGLECALENDAR_CREATE_EVENT": {"input_parameters": {
        "calendar_id": {"type": "string", "required": False},
        "summary": {"type": "string", "required": True},
        "start_datetime": {"type": "string", "required": True},
        "end_datetime": {"type": "string", "required": False},
        "description": {"type": "string", "required": False},
        "location": {"type": "string", "required": False},
        "attendees": {"type": "array", "required": False},
        "timezone": {"type": "string", "required": False}}},
    "/tools/GOOGLECALENDAR_QUICK_ADD": {"input_parameters": {
        "calendar_id": {"type": "string", "required": False},
        "text": {"type": "string", "required": True}}},
}
SEND_OK = {"successful": True, "error": None,
           "data": {"successful": True, "error": None,
                    "response_data": {"messageId": "m_123", "threadId": "t_9",
                                      "labelIds": ["SENT"]}}}
SEARCH_OK = {"successful": True, "error": None,
             "data": {"messages": [
                 {"MessageId": "m_1", "ThreadId": "t_1", "Subject": "Laporan bulanan",
                  "Sender": "budi@perusahaan.co.id", "Date": "Mon, 19 Oct 2026",
                  "Snippet": "Terlampir laporan…"},
                 {"MessageId": "m_2", "ThreadId": "t_2", "Subject": "Tagihan listrik",
                  "Sender": "no-reply@pln.co.id", "Date": "Sun, 18 Oct 2026",
                  "Snippet": "Tagihan Anda…"}]}}
CONNS_ACTIVE = {"items": [{"id": "ca_1", "toolkit": {"slug": "gmail"},
                           "status": "ACTIVE"},
                          {"id": "ca_2", "toolkit": {"slug": "github"},
                           "status": "ACTIVE"},
                          {"id": "ca_3", "toolkit": {"slug": "googlecalendar"},
                           "status": "ACTIVE"}]}
CONNS_NO_GMAIL = {"items": [{"id": "ca_2", "toolkit": {"slug": "github"},
                             "status": "ACTIVE"}]}
TOOLS_LIST = {"items": [
    {"slug": "GMAIL_FETCH_EMAILS", "name": "Fetch emails",
     "description": "Fetch emails from Gmail.",
     "input_parameters": {"query": {"required": False},
                          "max_results": {"required": False}}},
    {"slug": "GMAIL_SEND_EMAIL", "name": "Send Email",
     "description": "Send an email via Gmail.",
     "input_parameters": {"recipient_email": {"required": True},
                          "subject": {"required": True},
                          "body": {"required": True}}},
    {"slug": "OLD_TOOL", "name": "Old", "description": "x",
     "is_deprecated": True, "input_parameters": {}}]}


def scenario_full(execute_payload=SEND_OK, execute_status=200,
                  conns=CONNS_ACTIVE, discover=TOOLS_LIST):
    """Schema GETs, connection GET, execute POST, and discover, all wired."""
    def exec_fn(m, u, b):
        return execute_payload, execute_status
    scen = {
        "/connected_accounts": lambda m, u, b: (conns, 200),
        "/tools/execute/": lambda m, u, b: exec_fn(m, u, b),
        "/tools?": lambda m, u, b: (discover, 200),
    }
    for slug, payload in SCHEMAS.items():
        scen[slug] = (lambda p: (lambda m, u, b: (p, 200)))(payload)
    return scen


def load_plugin():
    path = os.path.join(ROOT, "plugins", "composio_actions.py")
    spec = importlib.util.spec_from_file_location("composio_actions", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load_plugin()
    KEY_CFG = {"api_key": "ck_test_123", "user_id": "aris"}

    # ── 0. no API key yet → setup guidance, no HTTP at all ──────────────────
    mod._config = lambda: {}
    said = mod.run({"action": "gmail_send", "to": "a@b.co", "subject": "s",
                    "body": "b"})
    check("run() without key explains setup",
          "belum disetel" in said.lower(), said[:60])

    mod._config = lambda: dict(KEY_CFG)

    # ── 1. gmail_send is two-step: confirm first, then execute ───────────────
    CALLS.clear()
    mod._urlopen = make_urlopen(scenario_full())
    send_args = {"action": "gmail_send", "to": "budi@perusahaan.co.id",
                 "subject": "Jadwal rapat", "body": "Rapat besok jam 9.",
                 "is_html": False, "attachment": ""}
    said = mod.run(dict(send_args))
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    check("gate: first call executes nothing",
          len(posts) == 0 and "KONFIRMASI DIPERLUKAN" in said
          and "budi@perusahaan.co.id" in said and "Jadwal rapat" in said,
          said[:90])
    said = mod.run(dict(send_args, confirm="ya"))
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    check("gate: confirmed call executes once",
          len(posts) == 1 and "/api/v3.1/tools/execute/GMAIL_SEND_EMAIL" in posts[0][1],
          posts[0][1] if posts else "none")
    sent = posts[0][2] if posts else {}
    check("send: body carries user_id",
          sent.get("user_id") == "aris", str(sent)[:80])
    check("send: friendly to→recipient_email via live schema",
          sent.get("arguments", {}).get("recipient_email") == "budi@perusahaan.co.id",
          str(sent.get("arguments")))
    check("send: blank/False friendly values dropped",
          "is_html" not in sent.get("arguments", {})
          and "attachment" not in sent.get("arguments", {}))
    check("send: success line + message_id",
          said.startswith("Email terkirim ke budi@perusahaan.co.id")
          and "m_123" in said, said[:90])
    check("send: no message-render noise after the line",
          "tanpa subjek" not in said, said[-90:])
    schema_gets = [c for c in CALLS if c[0] == "GET" and "/tools/GMAIL_SEND_EMAIL" in c[1]]
    check("send: schema fetched with toolkit_versions=latest",
          len(schema_gets) == 1 and "toolkit_versions=latest" in schema_gets[0][1])

    # ── 1b. a hallucinated address never even reaches confirmation ───────────
    CALLS.clear()
    mod._urlopen = make_urlopen(scenario_full())
    said = mod.run({"action": "gmail_send", "to": "rafael@example.com",
                    "subject": "s", "body": "raka"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    check("guard: reserved example.com blocked, no POST",
          len(posts) == 0 and "domain contoh" in said, said[:100])
    said = mod.run({"action": "gmail_send", "to": "budi",
                    "subject": "s", "body": "b"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    check("guard: malformed address blocked, no POST",
          len(posts) == 0 and "bukan alamat email" in said, said[:100])

    # ── 1c. a confirm with different arguments is a NEW request, not a yes ───
    CALLS.clear()
    mod._pending.clear()
    mod._urlopen = make_urlopen(scenario_full())
    mod.run({"action": "gmail_send", "to": "budi@perusahaan.co.id",
             "subject": "A", "body": "isi A"})
    said = mod.run({"action": "gmail_send", "to": "lain@perusahaan.co.id",
                    "subject": "A", "body": "isi A", "confirm": "ya"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    check("gate: swapped address cannot ride on an old confirm",
          len(posts) == 0 and "KONFIRMASI DIPERLUKAN" in said, said[:100])

    # ── 1d. a bare confirm with no pending record asks again, never runs ─────
    CALLS.clear()
    mod._pending.clear()
    mod._urlopen = make_urlopen(scenario_full())
    said = mod.run({"action": "gmail_send", "to": "budi@perusahaan.co.id",
                    "subject": "A", "body": "isi A", "confirm": "ya"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    check("gate: bare confirm without pending does not execute",
          len(posts) == 0 and "KONFIRMASI DIPERLUKAN" in said, said[:100])

    # ── 2. argument-name drift: alias picks the name the tool declares ──────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    mod._urlopen = make_urlopen(scenario_full())
    said = mod.run({"action": "gmail_draft", "to": "draf@perusahaan.co",
                    "subject": "Draf", "body": "isi draf"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    args = posts[0][2].get("arguments", {}) if posts else {}
    check("draft: 'to' maps to alias 'recipient' the schema declares",
          args.get("recipient") == "draf@perusahaan.co"
          and "recipient_email" not in args, str(args))
    check("draft: draft slug used", "/GMAIL_CREATE_EMAIL_DRAFT" in posts[0][1])
    check("draft: success line", said.startswith("Draft tersimpan"), said[:60])

    # ── 3. schema mismatch on a required arg → discover hint, no execute ────
    CALLS.clear()
    mod._schema_cache.clear()
    scen = scenario_full(conns=CONNS_ACTIVE)
    scen["/tools/GOOGLECALENDAR_CREATE_EVENT"] = lambda m, u, b: (
        {"input_parameters": {"judul_aneh": {"required": True},
                              "mulai_aneh": {"required": True}}}, 200)
    mod._urlopen = make_urlopen(scen)
    said = mod.run({"action": "calendar_create", "title": "Rapat",
                    "start": "2026-10-20T09:00:00"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    check("mismatch: no execute POST when required args unmatched",
          len(posts) == 0)
    check("mismatch: message names the tool + says discover",
          "GOOGLECALENDAR_CREATE_EVENT" in said and "discover" in said, said[:90])

    # ── 4. 404 on execute → slug drift hint ────────────────────────────────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    mod._urlopen = make_urlopen(scenario_full(
        execute_payload={"error": {"message": "Tool not found"}}, execute_status=404))
    mod.run({"action": "gmail_send", "to": "a@b.co", "subject": "s",
             "body": "b"})                                   # gate: confirm first
    said = mod.run({"action": "gmail_send", "to": "a@b.co", "subject": "s",
                    "body": "b", "confirm": "ya"})
    check("404: drift hint mentions discover",
          "discover" in said and "404" in said, said[:100])

    # ── 5. tool-level failure (successful:false) → spoken error ─────────────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    mod._urlopen = make_urlopen(scenario_full(
        execute_payload={"successful": True, "error": None,
                         "data": {"successful": False, "error": "Quota exceeded"}}))
    mod.run({"action": "gmail_send", "to": "a@b.co", "subject": "s",
             "body": "b"})                                   # gate: confirm first
    said = mod.run({"action": "gmail_send", "to": "a@b.co", "subject": "s",
                    "body": "b", "confirm": "ya"})
    check("tool failure: error text surfaced",
          "Quota exceeded" in said and "gagal" in said, said[:100])

    # ── 6. not connected → guidance, zero execute calls ────────────────────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    mod._urlopen = make_urlopen(scenario_full(conns=CONNS_NO_GMAIL))
    said = mod.run({"action": "gmail_send", "to": "a@b.co", "subject": "s",
                    "body": "b"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    check("not connected: no execute POST", len(posts) == 0)
    check("not connected: points at Connections panel",
          "Connections" in said and "Gmail" in said, said[:90])

    # ── 7. connection check fails open on transport error ───────────────────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    scen = scenario_full()
    scen["/connected_accounts"] = lambda m, u, b: (_ for _ in ()).throw(
        __import__("urllib.error", fromlist=["error"]).URLError("offline"))
    mod._urlopen = make_urlopen(scen)
    mod.run({"action": "gmail_send", "to": "a@b.co", "subject": "s",
             "body": "b"})                                   # gate: confirm first
    said = mod.run({"action": "gmail_send", "to": "a@b.co", "subject": "s",
                    "body": "b", "confirm": "ya"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    check("conn check fail-open: execute still attempted",
          len(posts) == 1 and said.startswith("Email terkirim"), said[:60])

    # ── 8. gmail_search rendering: PascalCase keys, ids carried ─────────────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    mod._urlopen = make_urlopen(scenario_full(execute_payload=SEARCH_OK))
    said = mod.run({"action": "gmail_search", "query": "from:budi"})
    check("search: 2 results announced",
          said.startswith("2 hasil:"), said[:60])
    check("search: subject + sender rendered",
          "Laporan bulanan" in said and "budi@perusahaan.co.id" in said)
    check("search: ids carried for follow-ups",
          "message_id: m_1" in said and "thread_id: t_1" in said)
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    args = posts[0][2].get("arguments", {}) if posts else {}
    check("search: query default max_results applied",
          args.get("query") == "from:budi" and args.get("max_results") == 10,
          str(args))

    # ── 9. calendar_list default calendar_id=primary ────────────────────────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    mod._urlopen = make_urlopen(scenario_full(
        execute_payload={"successful": True, "error": None,
                         "data": {"events": [
                             {"summary": "Rapat tim", "start": "2026-10-20T09:00:00+07:00",
                              "end": "2026-10-20T10:00:00+07:00", "location": "Ruang A",
                              "id": "ev_1",
                              "attendees": [{"email": "budi@x.co"}]}]}}))
    said = mod.run({"action": "calendar_list"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    args = posts[0][2].get("arguments", {}) if posts else {}
    check("calendar_list: default calendar primary",
          args.get("calendar_id") == "primary" and args.get("max_results") == 25,
          str(args))
    check("calendar_list: event rendered with id + attendees",
          "Rapat tim" in said and "ev_1" in said and "budi@x.co" in said, said[:120])

    # ── 10. calendar_create: default end = start + 1h ───────────────────────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    mod._urlopen = make_urlopen(scenario_full(
        execute_payload={"successful": True, "error": None,
                         "data": {"response_data": {"id": "ev_new",
                                                    "status": "confirmed"}}}))
    said = mod.run({"action": "calendar_create", "title": "Rapat Budi",
                    "start": "2026-10-20T09:00:00",
                    "attendees": ["budi@x.co", "ani@y.co"]})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    args = posts[0][2].get("arguments", {}) if posts else {}
    check("create: title→summary, start→start_datetime",
          args.get("summary") == "Rapat Budi"
          and args.get("start_datetime") == "2026-10-20T09:00:00", str(args))
    check("create: end defaults to start + 1h",
          args.get("end_datetime") == "2026-10-20T10:00:00", str(args))
    check("create: attendees list passed through",
          args.get("attendees") == ["budi@x.co", "ani@y.co"], str(args))
    check("create: success line + event_id",
          said.startswith("Acara “Rapat Budi” dibuat") and "ev_new" in said, said[:90])

    # ── 11. calendar_quick_add ──────────────────────────────────────────────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    mod._urlopen = make_urlopen(scenario_full(
        execute_payload={"successful": True, "error": None,
                         "data": {"response_data": {"id": "ev_q",
                                                    "summary": "Rapat Budi",
                                                    "start": "2026-10-21T15:00:00+07:00"}}}))
    said = mod.run({"action": "calendar_quick_add",
                    "text": "rapat dengan Budi besok jam 3 sore"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    args = posts[0][2].get("arguments", {}) if posts else {}
    check("quick_add: text mapped verbatim",
          args.get("text") == "rapat dengan Budi besok jam 3 sore"
          and args.get("calendar_id") == "primary", str(args))
    check("quick_add: success line names the sentence",
          said.startswith("Acara dibuat dari"), said[:70])

    # ── 12. discover: schemas listed, deprecated dropped, cap noted ─────────
    CALLS.clear()
    mod._urlopen = make_urlopen(scenario_full())
    said = mod.run({"action": "discover", "app": "gmail"})
    check("discover: toolkit reflected",
          said.startswith("gmail: 2 tool tersedia"), said[:60])
    check("discover: required args shown",
          "wajib: recipient_email, subject, body" in said)
    check("discover: run_tool guidance appended",
          "run_tool" in said)

    # ── 13. run_tool: arguments pass through verbatim ───────────────────────
    CALLS.clear()
    mod._urlopen = make_urlopen(scenario_full(
        execute_payload={"successful": True, "error": None,
                         "data": {"response_data": {"ok": True,
                                                    "starred": 1}}}))
    said = mod.run({"action": "run_tool", "tool": "GITHUB_STAR_REPO",
                    "arguments": {"owner": "composiohq", "repo": "composio",
                                  "page": 1}})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    args = posts[0][2].get("arguments", {}) if posts else {}
    check("run_tool: slug + arguments verbatim",
          "/GITHUB_STAR_REPO" in posts[0][1]
          and args == {"owner": "composiohq", "repo": "composio", "page": 1},
          str(args))
    check("run_tool: payload rendered",
          "ok" in said and "starred" in said, said[:60])

    # ── 14. missing required params → ask, no HTTP ─────────────────────────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    mod._urlopen = make_urlopen(scenario_full())
    said = mod.run({"action": "gmail_send", "to": "a@b.co"})
    posts = [c for c in CALLS if c[0] == "POST" and "/tools/execute/" in c[1] and not _history_read(c)]
    check("missing params: no execute POST", len(posts) == 0)
    check("missing params: names what is missing",
          "subject" in said and "body" in said, said[:90])

    # ── 15. 401 → Indonesian actionable hint ────────────────────────────────
    CALLS.clear()
    mod._schema_cache.clear()
    mod._conn_cache.update(ts=0.0, ok=False)
    mod._urlopen = make_urlopen(scenario_full(
        execute_payload={"error": {"message": "Invalid API key"}},
        execute_status=401))
    mod.run({"action": "gmail_send", "to": "a@b.co", "subject": "s",
             "body": "b"})                                   # gate: confirm first
    said = mod.run({"action": "gmail_send", "to": "a@b.co", "subject": "s",
                    "body": "b", "confirm": "ya"})
    check("401: points at dashboard.composio.dev",
          "dashboard.composio.dev" in said and "401" in said, said[:90])

    print("\n".join(REPORT))
    print(f"\nERRORS: {'none' if not ERRORS else ', '.join(ERRORS)}")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())
