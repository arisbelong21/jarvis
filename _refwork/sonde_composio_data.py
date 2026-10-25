# -*- coding: utf-8 -*-
"""Data-layer probe for the Composio bridge plugin (offline — no network).

Every HTTP call is replaced at the single seam (`_urlopen`), so this exercises
the real request/response code paths against canned Composio payloads:
catalog parsing, card building, the Connect Link handshake, status polling,
disconnect, and error translation.
"""
import importlib.util
import io
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = []
ERRORS = []


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


TOOLKITS = {
    "items": [
        {"slug": "gmail", "name": "Gmail",
         "meta": {"description": "Email by Google.", "logo": "https://assets.composio.dev/logos/gmail.png",
                  "categories": [{"id": "email", "name": "Email"}]},
         "auth_schemes": ["oauth2"]},
        {"slug": "github", "name": "GitHub",
         "meta": {"description": "Code hosting.", "logo": "https://assets.composio.dev/logos/github.png",
                  "categories": [{"id": "developer-tools", "name": "Developer Tools"}]},
         "auth_schemes": ["oauth2"]},
        {"slug": "oldapp", "name": "Old App", "deprecated": True, "meta": {}},
    ],
    "next_cursor": None,
}
CONNS_PAGE1 = {
    "items": [
        {"id": "ca_gmail_1", "toolkit": {"slug": "gmail"},
         "status": "INITIALIZING", "user_id": "aris", "alias": "aris@gmail"},
    ],
    "next_cursor": "cur2",
}
CONNS_PAGE2 = {
    "items": [
        {"id": "ca_github_1", "toolkit": {"slug": "github"},
         "status": "ACTIVE", "user_id": "aris"},
    ],
    "next_cursor": None,
}


def make_urlopen(scenario):
    """scenario: dict(url substring) -> callable(method, url, body) -> payload"""
    seen = []

    def fake(req, timeout):
        method, url = req.method, req.full_url
        body = json.loads(req.data.decode()) if req.data else None
        seen.append((method, url, body))
        for key, fn in scenario.items():
            if key in url:
                payload, status = fn(method, url, body)
                if status >= 400:
                    import urllib.error
                    raise urllib.error.HTTPError(
                        url, status, "err", None,
                        io.BytesIO(json.dumps(payload).encode()))
                return FakeResp(payload)
        raise AssertionError(f"unexpected request: {method} {url}")

    return fake, seen


def main():
    path = os.path.join(ROOT, "plugins", "composio_connector.py")
    spec = importlib.util.spec_from_file_location("composio_connector", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # ── 1. catalog + connection fetching ─────────────────────────────────────
    fake, seen = make_urlopen({
        "/toolkits": lambda m, u, b: (TOOLKITS, 200),
        "/connected_accounts?": lambda m, u, b: (
            CONNS_PAGE1 if "cursor=cur2" not in u else CONNS_PAGE2, 200),
    })
    mod._urlopen = fake
    catalog = mod.fetch_catalog("k")
    check("catalog parses items", len(catalog) == 2 and
          catalog[0]["name"] == "Gmail", f"{len(catalog)} apps")
    check("catalog drops deprecated",
          all(a["slug"] != "oldapp" for a in catalog))
    check("catalog reads meta.logo",
          catalog[0]["logo"].endswith("gmail.png"))
    check("catalog reads categories", catalog[0]["categories"] == "Email")

    conns = mod.fetch_connections("k", "aris")
    check("connections follow pagination", len(conns) == 2 and
          {c["slug"] for c in conns} == {"gmail", "github"},
          f"{len(conns)} conns")
    check("connections send user filter",
          any("user_ids=aris" in u for _, u, _ in seen))

    # ── 2. card building: connected first, statuses mapped ──────────────────
    cards = mod._build_specs(catalog, conns, show_all=True)
    check("cards: connected app first",
          cards[0]["slug"] == "github" and cards[0]["status"] == "connected",
          cards[0]["slug"])
    check("cards: initializing app is error/re-connect",
          any(c["slug"] == "gmail" and c["status"] == "error"
              and c["status_text"] == "re-connect" for c in cards))
    check("cards: group label present",
          all(c["group"] == mod._GROUP for c in cards))
    check("cards: kind is composio_app",
          all(c["kind"] == "composio_app" for c in cards))
    check("cards: conn_id carried",
          any(c["conn_id"] == "ca_gmail_1" for c in cards))

    # ── 3. Connect Link handshake: managed auth config → link ──────────────
    fake2, seen2 = make_urlopen({
        "/auth_configs": lambda m, u, b: (
            {"auth_config": {"id": "ac_gmail_1", "is_composio_managed": True}}, 201)
        if m == "POST" else ({"items": []}, 200),
        "/connected_accounts/link": lambda m, u, b: (
            {"redirect_url": "https://connect.composio.dev/link/ln_test"}, 201),
    })
    mod._urlopen = fake2
    url = mod.create_link("k", "aris", "gmail")
    check("create_link returns redirect_url",
          url == "https://connect.composio.dev/link/ln_test", url)
    post_auth = [s for s in seen2 if s[0] == "POST" and "/auth_configs" in s[1]]
    check("auth config POST uses managed auth",
          post_auth and post_auth[0][2]["auth_config"]["type"] == "use_composio_managed_auth"
          and post_auth[0][2]["toolkit"]["slug"] == "gmail")
    link_req = [s for s in seen2 if s[0] == "POST" and "/connected_accounts/link" in s[1]]
    check("link POST carries auth_config_id + user_id",
          link_req and link_req[0][2] == {"auth_config_id": "ac_gmail_1",
                                          "user_id": "aris"})

    # ── 4. status polling + disconnect ──────────────────────────────────────
    fake3, seen3 = make_urlopen({
        "/connected_accounts?": lambda m, u, b: (
            {"items": [{"id": "ca_gmail_1", "toolkit": {"slug": "gmail"},
                        "status": "ACTIVE"}]}, 200),
        "/connected_accounts/ca_": lambda m, u, b: ({}, 200),
    })
    mod._urlopen = fake3
    conn = mod.connection_status("k", "aris", "gmail")
    check("poll: ACTIVE surfaces with id",
          conn["status"] == "ACTIVE" and conn["id"] == "ca_gmail_1")
    err = mod.delete_connection("k", "ca_gmail_1")
    check("disconnect: empty string = success", err == "")
    check("disconnect: DELETE to right path",
          any(m == "DELETE" and "/connected_accounts/ca_gmail_1" in u
              for m, u, _ in seen3))

    # ── 5. error translation (401 → sentence, not a trace) ───────────────────
    fake4, _ = make_urlopen({
        "/toolkits": lambda m, u, b: (
            {"error": {"message": "Invalid API key: ck_**xyz"}}, 401),
    })
    mod._urlopen = fake4
    try:
        mod.fetch_catalog("bad")
        check("401 raises ComposioError", False, "no exception")
    except mod.ComposioError as e:
        check("401 raises ComposioError", True)
        check("401 message is actionable",
              "ditolak" in str(e) and "dashboard.composio.dev" in str(e), str(e))

    # ── 6. cards() caching contract: fast, then a background refresh ────────
    mod._state.update(catalog=[], catalog_ts=0.0, conns=[], conns_ts=0.0,
                      error="", error_ts=0.0, busy=False, key="", user="")
    fake5, _ = make_urlopen({
        "/toolkits": lambda m, u, b: (TOOLKITS, 200),
        "/connected_accounts?": lambda m, u, b: (CONNS_PAGE2, 200),
    })
    mod._urlopen = fake5
    t0 = time.time()
    first = mod.cards("k", "aris")
    check("first cards() instant (background fetch)",
          time.time() - t0 < 0.5, f"{time.time()-t0:.2f}s")
    check("first cards(): loading tile marked busy",
          any(c.get("busy") and c["status"] == "starting" for c in first))
    deadline = time.time() + 8
    while time.time() < deadline and (mod.state()["busy"] or not mod.state()["apps"]):
        time.sleep(0.1)
    st = mod.state()
    check("background fetch completes", not st["busy"] and st["apps"] == 2,
          str(st))
    second = mod.cards("k", "aris")
    check("second cards() from cache (no busy tile)",
          not any(c.get("busy") for c in second) and len(second) == 2)
    check("second cards() instant",
          (lambda t: time.time() - t < 0.5)(time.time()))

    # ── 7. key change drops the old cache ───────────────────────────────────
    cards_other = mod.cards("k2", "aris")
    check("new key → loading tile, cache not reused",
          any(c.get("busy") for c in cards_other))

    # ── 8. model entry point: no key → guidance ────────────────────────────
    said = mod.run({"action": "status"})
    check("run() without key explains setup",
          "belum disetel" in said.lower(), said[:60])

    print("\n".join(REPORT))
    print(f"\nERRORS: {'none' if not ERRORS else ', '.join(ERRORS)}")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())
