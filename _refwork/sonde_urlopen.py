"""Bedah kenapa urllib dapat balasan KOSONG dari backend.composio.dev
sementara curl dapat data penuh. Uji: (1) gaya persis plugin, (2) tanpa proxy.
"""
import os
import urllib.parse
import urllib.request

key = "ak_L4S_h1S7YBoMG_rejL80"
url = ("https://backend.composio.dev/api/v3.1/toolkits?"
       + urllib.parse.urlencode({"limit": 200, "sort_by": "alphabetically"}))

print("ENV http_proxy :", os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY"))
print("ENV https_proxy:", os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY"))
print("urllib proxies :", urllib.request.getproxies())

req = urllib.request.Request(url, method="GET")
req.add_header("x-api-key", key)
req.add_header("Content-Type", "application/json")
req.add_header("Accept", "application/json")

# (1) persis seperti plugin: urlopen polos
try:
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", "replace")
        print(f"[1 POLOS]  status={resp.status}  len={len(raw)}  ct={resp.headers.get('content-type')}")
        print("        head:", raw[:120].replace("\n", " "))
except Exception as e:                       # noqa: BLE001
    print("[1 POLOS] GAGAL:", type(e).__name__, str(e)[:200])

# (2) opener tanpa proxy (direct)
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", "replace")
        print(f"[2 DIRECT] status={resp.status}  len={len(raw)}  ct={resp.headers.get('content-type')}")
        print("        head:", raw[:120].replace("\n", " "))
except Exception as e:                       # noqa: BLE001
    print("[2 DIRECT] GAGAL:", type(e).__name__, str(e)[:200])

# (3) tanpa header Content-Type (persis gaya curl)
req2 = urllib.request.Request(url, method="GET")
req2.add_header("x-api-key", key)
req2.add_header("Accept", "application/json")
req2.add_header("User-Agent", "curl/8.5.0")
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req2, timeout=25) as resp:
        raw = resp.read().decode("utf-8", "replace")
        print(f"[3 DIRECT+UA] status={resp.status}  len={len(raw)}")
        print("        head:", raw[:120].replace("\n", " "))
except Exception as e:                       # noqa: BLE001
    print("[3 DIRECT+UA] GAGAL:", type(e).__name__, str(e)[:200])
