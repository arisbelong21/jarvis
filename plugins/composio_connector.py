"""
Composio bridge — the Connections panel's window onto 1,600+ apps.

Composio (composio.dev) runs the OAuth dance for hundreds of services and keeps
the tokens. This plugin is the glue that turns that into something Jarvis can
draw: it holds the API key, lists the app catalog, reports which accounts are
connected, and opens a Connect Link for the ones that are not. The panel
(ui.py) knows nothing about Composio — it draws the card specs this module
hands back, exactly like it draws a plugin's own settings section.

Transport is plain urllib on purpose: zero new dependencies, and every failure
arrives as a sentence the person reading the panel can act on, never as a
stack trace.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# Katalog terkurasi: app yang umum dipakai untuk pekerjaan. Semua slug di
# bawah diverifikasi hidup di katalog DAN mendukung Composio managed-auth
# (bisa disambungkan otomatis). Bisa diubah dari kartu Composio (satu slug
# per baris) — tampilannya ikut pilihan bos.
_FAV_APPS = [
    "gmail", "googlecalendar", "googlesheets", "googledrive", "googledocs",
    "googletasks", "googleslides", "outlook", "slack", "notion", "github",
    "gitlab", "discord", "trello", "asana", "jira", "linear", "todoist",
    "zoom", "dropbox", "hubspot", "salesforce", "airtable", "figma",
    "confluence", "whatsapp", "mailchimp", "youtube", "linkedin", "reddit",
    "zendesk", "intercom", "monday", "clickup", "basecamp",
    "microsoft_teams", "calendly", "box", "typeform", "quickbooks",
]

PLUGIN = {
    "name": "composio_connector",
    "description": (
        "Reports which external apps are connected through Composio "
        "(Gmail, GitHub, Notion, Slack, Google Calendar and ~1,600 more), and "
        "opens or closes those connections. Use when the user asks what is "
        "connected, wants to connect or disconnect an app, or talks about "
        "integrations. Do not use for local computer settings — that is the "
        "system tools' job."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["status", "refresh"],
                "description": ("status = report connected apps and counts; "
                                "refresh = re-fetch the catalog and connections now"),
            },
        },
        "required": [],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "composio",
    "title": "🔗  COMPOSIO",
    "fields": [
        {"key": "api_key", "label": "Composio API Key", "type": "password",
         "placeholder": "dashboard.composio.dev → Settings → API Keys → copy"},
        {"key": "user_id", "label": "User ID (pemilik koneksi)", "type": "text",
         "default": "aris",
         "placeholder": "mis. aris — bebas, yang penting konsisten"},
        {"key": "favourite_apps", "label": "App yang ditampilkan di panel (satu slug per baris)",
         "type": "textarea", "default": "\n".join(_FAV_APPS),
         "placeholder": "gmail\nslack\ngithub — kosongkan untuk pakai daftar bawaan"},
        {"key": "show_all_apps", "label": "Tampilkan katalog luas (400 app teratas; 1.600+ lainnya lewat pencarian)",
         "type": "toggle", "default": False},
    ],
    "action": {"label": "TES KUNCI API", "run": lambda values: _test_key(values or {})},
}

_BASE = "https://backend.composio.dev"
_API_PATHS = ("/api/v3.1", "/api/v3")   # v3.1 is current; v3 kept as fallback
_TIMEOUT = 25
_MAX_APPS = 400                 # panel sanity: 1,600 tiles is a scroll marathon
_SEARCH_CAP = 100              # search results: enough to skim, never a wall
_CATALOG_TTL = 300.0
_CONN_TTL = 30.0
_ERROR_RETRY_S = 30.0
_GROUP = "APLIKASI COMPOSIO  ·  composio.dev"


class ComposioError(RuntimeError):
    """A failure carrying a message written for the person reading the panel."""


def _urlopen(req, timeout):
    """The single seam every HTTP call goes through — the audit probe replaces
    this instead of touching the network."""
    return urllib.request.urlopen(req, timeout=timeout)


def _err_msg(code: int, raw: str) -> str:
    try:
        data = json.loads(raw)
        msg = (data.get("error") or {}).get("message") or ""
    except Exception:
        msg = raw[:200]
    if code == 401:
        return ("API key ditolak Composio (401). Periksa kunci di kartu "
                "Composio — salin ulang dari dashboard.composio.dev.")
    if code == 429:
        return "Composio membatasi laju permintaan (429). Tunggu sebentar."
    return f"Composio menolak permintaan (HTTP {code}): {msg or 'tanpa keterangan'}"


def _api(key: str, method: str, path: str, params: dict | None = None,
         body: dict | None = None):
    """One REST call. Returns parsed JSON. Raises ComposioError with a human
    message; falls back to the older API version once on a 404."""
    last = ""
    for prefix in _API_PATHS:
        url = _BASE + prefix + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("x-api-key", key)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with _urlopen(req, _TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            msg = _err_msg(e.code, raw)
            if e.code == 404 and prefix != _API_PATHS[-1]:
                last = msg
                continue                    # older deployment: try the other version
            raise ComposioError(msg)
        except urllib.error.URLError as e:
            raise ComposioError(
                f"Tidak bisa menghubungi Composio ({getattr(e, 'reason', e)}). "
                "Cek koneksi internet.")
        except ComposioError:
            raise
        except Exception as e:
            raise ComposioError(f"Composio: {e}")
        try:
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            raise ComposioError("Composio mengirim balasan yang tidak bisa dibaca.")
    raise ComposioError(last or "Composio menolak permintaan.")

def _toolkit_deprecated(it: dict) -> bool:
    """Read a toolkit's deprecation flag without trusting its shape.

    The field used to arrive as a plain boolean. It now comes back as an
    object ({"toolkitId": ...}) on EVERY toolkit — reference/lineage data, not
    a deprecation state. Casting that object with bool() marked all 1,600+
    toolkits deprecated and emptied the panel, so only an explicit boolean
    or an explicit flag inside the object counts as deprecated.
    """
    d = it.get("deprecated")
    if isinstance(d, bool):
        return d
    if isinstance(d, dict):
        for k in ("deprecated", "is_deprecated"):
            if k in d:
                return bool(d[k])
        return False                    # reference object — not a deprecation
    return bool(d)


def fetch_catalog(key: str) -> list[dict]:
    """Every toolkit the project can see — paged, newest naming, no deprecated."""
    apps, cursor, pages = [], None, 0
    while pages < 10:
        params = {"limit": 200, "sort_by": "alphabetically"}
        if cursor:
            params["cursor"] = cursor
        data = _api(key, "GET", "/toolkits", params=params)
        items = data.get("items") or data.get("toolkits") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            meta = it.get("meta") or {}
            cats = [c for c in (meta.get("categories") or []) if isinstance(c, dict)]
            apps.append({
                "slug": str(it.get("slug") or ""),
                "name": str(it.get("name") or it.get("slug") or ""),
                "desc": str(meta.get("description") or "").strip(),
                "logo": str(meta.get("logo") or ""),
                "categories": ", ".join(str(c.get("name") or "") for c in cats),
                "deprecated": _toolkit_deprecated(it),
                "managed": bool(it.get("composio_managed_auth_schemes")),
                "no_auth": bool(it.get("no_auth")),
                "auth_schemes": ", ".join(str(s) for s in (it.get("auth_schemes") or [])),
            })
        cursor = data.get("next_cursor") or data.get("nextCursor")
        pages += 1
        if not cursor or not items:
            break
    return [a for a in apps if a["slug"] and not a["deprecated"]]


def fetch_connections(key: str, user_id: str) -> list[dict]:
    """Connected accounts for one user — the raw material for card badges."""
    out, cursor, pages = [], None, 3
    while pages:
        params = {"limit": 200}
        if user_id:
            params["user_ids"] = user_id
        if cursor:
            params["cursor"] = cursor
        data = _api(key, "GET", "/connected_accounts", params=params)
        items = data.get("items") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            tk = it.get("toolkit") or {}
            out.append({
                "id": str(it.get("id") or ""),
                "slug": str(tk.get("slug") or ""),
                "status": str(it.get("status") or "").upper(),
                "is_disabled": bool(it.get("is_disabled")),
                "alias": str(it.get("alias") or ""),
            })
        cursor = data.get("next_cursor") or data.get("nextCursor")
        pages -= 1
        if not cursor or not items:
            break
    return out


def connection_status(key: str, user_id: str, slug: str) -> dict:
    """One toolkit's best connection: ACTIVE wins, otherwise the latest."""
    try:
        conns = fetch_connections(key, user_id)
    except ComposioError:
        raise
    best = None
    for c in conns:
        if c["slug"] != slug:
            continue
        if c["status"] == "ACTIVE" or best is None:
            best = c
    return best or {"id": "", "slug": slug, "status": "NONE", "alias": ""}


def _find_auth_config(node, slug: str):
    """Recursive sweep for an auth-config id — the list endpoint's envelope
    is not something worth betting the UI on."""
    if isinstance(node, dict):
        tk = node.get("toolkit")
        if isinstance(tk, dict) and str(tk.get("slug") or "") == slug:
            for k in ("id", "nanoid", "auth_config_id"):
                if node.get(k):
                    return str(node[k])
        for v in node.values():
            r = _find_auth_config(v, slug)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_auth_config(v, slug)
            if r:
                return r
    return None


def _ensure_auth_config(key: str, slug: str) -> str:
    """A Composio-managed auth config for the toolkit — create one if the
    project has none. The POST is the documented path; the GET sweep is the
    fallback for when it is refused."""
    try:
        data = _api(key, "POST", "/auth_configs", body={
            "toolkit": {"slug": slug},
            "auth_config": {"type": "use_composio_managed_auth",
                            "credentials": {},
                            "restrict_to_following_tools": []},
        })
        ac = data.get("auth_config") if isinstance(data, dict) else None
        ac = ac if isinstance(ac, dict) else (data if isinstance(data, dict) else {})
        for k in ("id", "nanoid", "auth_config_id"):
            if ac.get(k):
                return str(ac[k])
    except ComposioError:
        pass
    try:
        data = _api(key, "GET", "/auth_configs", params={"limit": 200})
        found = _find_auth_config(data, slug)
        if found:
            return found
    except ComposioError:
        pass
    raise ComposioError(f"Tidak bisa menyiapkan auth config untuk '{slug}'.")


def create_link(key: str, user_id: str, slug: str) -> str:
    """The Connect Link the browser is pointed at. One call, one URL."""
    ac_id = _ensure_auth_config(key, slug)
    data = _api(key, "POST", "/connected_accounts/link",
                body={"auth_config_id": ac_id, "user_id": user_id})
    url = str(data.get("redirect_url") or data.get("redirectUrl") or "")
    if not url:
        raise ComposioError("Composio tidak memberi tautan otorisasi.")
    return url


def delete_connection(key: str, conn_id: str) -> str:
    """Disconnect one connected account. Empty string means success."""
    if not conn_id:
        return "Tidak ada koneksi untuk diputus."
    _api(key, "DELETE", f"/connected_accounts/{urllib.parse.quote(conn_id)}")
    return ""

# ── cache: the panel must never block on the network ─────────────────────────
_lock = threading.Lock()
_state: dict = {
    "catalog": [], "catalog_ts": 0.0,
    "conns": [], "conns_ts": 0.0,
    "error": "", "error_ts": 0.0,
    "busy": False, "gen": 0,
    "key": "", "user": "",
}


def _fresh(ts: float, ttl: float) -> bool:
    return bool(ts) and (time.time() - ts) < ttl


def invalidate() -> None:
    """Drop the fresh marks — the next cards() call re-fetches from Composio."""
    with _lock:
        _state["catalog_ts"] = 0.0
        _state["conns_ts"] = 0.0


def state() -> dict:
    """What the panel polls to know a background refresh finished."""
    with _lock:
        return {"busy": _state["busy"], "error": _state["error"],
                "gen": _state["gen"], "apps": len(_state["catalog"]),
                "connected": sum(1 for c in _state["conns"]
                                 if c["status"] == "ACTIVE")}


def _card(kind_id: str, name: str, desc: str, status: str, busy: bool = False) -> dict:
    return {"kind": "composio_app", "id": f"composio:{kind_id}",
            "name": name, "desc": desc, "logo": "", "slug": "",
            "status": status, "status_text": "", "group": _GROUP,
            "fields": [], "values": {}, "busy": busy}


def fav_slugs(raw) -> list[str]:
    """Normalise the favourites setting. The panel stores a textarea as a list
    of lines; the config may still hold newline/comma text. Empty or None →
    the built-in curated list, so the panel never ends up blank by accident."""
    if raw is None:
        return list(_FAV_APPS)
    items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(",", "\n").split("\n")
    out = [str(s).strip().lower() for s in items]
    out = [s for s in out if s]
    return out or list(_FAV_APPS)


def _build_specs(catalog: list[dict], conns: list[dict], show_all: bool,
                 favs=None, search: str = "") -> list[dict]:
    active, problem = {}, {}
    for c in conns:
        if c["slug"] in active:
            continue
        if c["status"] == "ACTIVE":
            active[c["slug"]] = c
        else:
            problem.setdefault(c["slug"], c)

    def one(app: dict) -> dict:
        slug = app["slug"]
        if slug in active:
            status, text = "connected", "✓ aktif"
        elif slug in problem:
            status, text = "error", "re-connect"
        elif app["no_auth"]:
            status, text = "connected", "✓ tanpa login"
        else:
            status, text = "setup", "＋"
        return {
            "kind": "composio_app",
            "id": f"composio:{slug}",
            "name": app["name"],
            "desc": app["desc"] or app["categories"] or "Aplikasi eksternal via Composio",
            "logo": app["logo"],
            "slug": slug,
            "conn_id": (active.get(slug) or problem.get(slug) or {}).get("id") or "",
            "status": status,
            "status_text": text,
            "group": _GROUP,
            "auth_scheme": app["auth_schemes"],
            "keywords": f"{app['name']} {slug} {app['categories']} composio",
            "fields": [], "values": {}, "managed": app["managed"],
        }

    needle = (search or "").strip().lower()
    if needle:
        # Search covers the WHOLE catalog — connected apps included (they just
        # render with their "✓ aktif" status), nothing prepended, one cap. The
        # curated view and the 400-tile cap must never hide an app from the
        # search box: "google sheets" finds Google Sheets in curated mode.
        matched = [a for a in catalog
                   if needle in (f"{a['name']} {a['slug']} {a['categories']} "
                                 f"composio").lower()]
        return [one(a) for a in matched[:_SEARCH_CAP]]

    # Connected first, then the ones needing attention, then everything else —
    # the top of the grid is where bos looks for "what do I already have".
    head = [a for a in catalog if a["slug"] in active]
    head += [a for a in catalog
             if a["slug"] in problem and a["slug"] not in active]
    specs = [one(a) for a in head]
    rest = [a for a in catalog if a["slug"] not in active and a["slug"] not in problem]

    if show_all:
        specs.extend(one(a) for a in rest[:_MAX_APPS])
        if len(rest) > _MAX_APPS:
            specs.append(_card(
                "__more__",
                f"… dan {len(rest) - _MAX_APPS} app lainnya",
                "Pakai kotak pencarian (mencakup semua 1.600+ app) "
                "untuk menemukannya.",
                "paused"))
        return specs

    # Curated view: connected + favourites first; the other 1,500 tools stay
    # one tile away instead of flooding the panel.
    favs = fav_slugs(favs)
    fav_set = set(favs)
    picked = [a for a in rest if a["slug"] in fav_set]
    specs.extend(one(a) for a in picked)
    hidden = len(rest) - len(picked)
    if hidden > 0:
        specs.append(_card(
            "__more__",
            f"… dan {hidden} app lainnya",
            "Pakai kotak pencarian (mencakup semua 1.600+ app), atau "
            "aktifkan 'Katalog luas' di kartu Composio.",
            "paused"))
    return specs


def cards(key: str, user_id: str = "aris", show_all: bool = True,
          favs=None, search: str = "") -> list[dict]:
    """Card specs for the panel. Fast path first: whatever is cached is drawn
    immediately. A stale cache starts a background refresh and the panel polls
    for it — opening Connections never waits on the network.

    show_all=False (default in the card) shows only the curated favourites;
    everything else collapses into one “…dan N lainnya” tile. A non-empty
    search ignores both and matches the whole catalog, capped at _SEARCH_CAP."""
    key = str(key or "").strip()
    if not key:
        return []
    favs = fav_slugs(favs)
    with _lock:
        if _state["key"] != key or _state["user"] != user_id:
            # credentials changed: what is cached belongs to someone else
            _state.update(catalog=[], catalog_ts=0.0, conns=[], conns_ts=0.0,
                          error="", error_ts=0.0, key=key, user=user_id)
        catalog = list(_state["catalog"])
        conns = list(_state["conns"])
        error, busy = _state["error"], _state["busy"]
        stale = (not _fresh(_state["catalog_ts"], _CATALOG_TTL)
                 or not _fresh(_state["conns_ts"], _CONN_TTL))
        error_fresh = _fresh(_state["error_ts"], _ERROR_RETRY_S)

    specs = _build_specs(catalog, conns, show_all, favs, search)
    if error:
        specs.insert(0, _card("__error__", "Composio", error, "error"))
    if stale:
        if not busy and not error_fresh:
            threading.Thread(target=_refresh_worker,
                             args=(key, user_id), daemon=True).start()
            busy = True
        if not catalog and not error:
            specs.insert(0, _card("__loading__", "Composio",
                                  "Memuat katalog app dari composio.dev…",
                                  "starting", busy=True))
        elif busy and not error:
            specs.append(_card("__refresh__", "Composio",
                               "Menyegarkan katalog…", "starting", busy=True))
    return specs


def _refresh_worker(key: str, user_id: str) -> None:
    with _lock:
        if _state["busy"]:
            return
        _state["busy"] = True
    try:
        catalog = fetch_catalog(key)
        conns = fetch_connections(key, user_id)
        with _lock:
            _state.update(catalog=catalog, catalog_ts=time.time(),
                          conns=conns, conns_ts=time.time(),
                          error="", error_ts=0.0, busy=False,
                          gen=_state["gen"] + 1)
    except ComposioError as e:
        with _lock:
            _state.update(error=str(e), error_ts=time.time(),
                          busy=False, gen=_state["gen"] + 1)
    except Exception as e:                      # noqa: BLE001 - panel shows it
        with _lock:
            _state.update(error=f"Composio: {e}", error_ts=time.time(),
                          busy=False, gen=_state["gen"] + 1)


def _test_key(values: dict):
    """The TES KUNCI API button on the Composio card."""
    key = str((values or {}).get("api_key") or "").strip()
    if not key:
        return False, "Belum ada API key yang dimasukkan."
    try:
        _api(key, "GET", "/toolkits", params={"limit": 1})
        return True, "Kunci diterima Composio ✓"
    except ComposioError as e:
        return False, str(e)


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Voice entry point: a short, true readout of what is connected."""
    action = str((parameters or {}).get("action") or "status").strip().lower()
    try:
        from memory.config_manager import get_plugin_config
        cfg = get_plugin_config("composio")
    except Exception:
        cfg = {}
    key = str(cfg.get("api_key") or "").strip()
    if not key:
        return ("Composio belum disetel — buka panel Connections, klik kartu "
                "Composio, lalu masukkan API key dari dashboard.composio.dev.")
    user = str(cfg.get("user_id") or "aris").strip() or "aris"
    if action == "refresh":
        invalidate()
    with _lock:
        busy = _state["busy"]
    cards(key, user)                       # triggers a background refresh if stale
    if action == "refresh" or busy:
        for _ in range(40):                 # up to ~8s, then answer with what we have
            time.sleep(0.2)
            with _lock:
                if not _state["busy"]:
                    break
    st = state()
    if st["error"]:
        return f"Composio bermasalah: {st['error']}"
    with _lock:
        active = [c for c in _state["conns"] if c["status"] == "ACTIVE"]
        names = {}
        for a in _state["catalog"]:
            names[a["slug"]] = a["name"]
    if not st["apps"]:
        return "Composio belum bisa dibaca — coba lagi sebentar lagi."
    listed = ", ".join(names.get(c["slug"], c["slug"]) for c in active[:8])
    if active:
        extra = f" dan {len(active) - 8} lagi" if len(active) > 8 else ""
        return (f"Composio: {len(active)} app tersambung ({listed}{extra}) "
                f"dari {st['apps']} app tersedia. Sambung atau putuskan "
                "lewat panel Connections.")
    return (f"Composio siap: {st['apps']} app tersedia, belum ada yang "
            "tersambung. Buka panel Connections untuk memilih.")
