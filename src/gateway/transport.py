"""HTTPS-only for anything that carries a secret (setup, admin, agent API).

PaaS hosts terminate TLS at their proxy and forward plain HTTP with X-Forwarded-Proto: https,
so that header counts. A browser talking to the server directly over http:// never sends it.
Localhost stays allowed for development.
"""

from __future__ import annotations

from aiohttp import web

PROTECTED_PREFIXES = ("/setup", "/admin/", "/v1/")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}

INSECURE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Use HTTPS</title><link rel="stylesheet" href="/static/arcade.css"></head>
<body><div class="page">
<header class="top"><span class="wordmark">Telegram Agent Gateway</span><span class="pill err">Not secure</span></header>
<section class="panel"><h2>Open this page over HTTPS</h2>
<p class="lede">Setup sends your passwords and Telegram keys. Over plain http:// anyone on the network path can read them, so this server refuses.</p>
<ol class="howto">
<li><strong>Render, Fly:</strong> use the <code>https://</code> address your host shows (e.g. <code>https://your-app.onrender.com</code>), not the server IP.</li>
<li><strong>Your own server (VPS, Docker):</strong> put an HTTPS proxy in front, e.g. Caddy with your domain, then open <code>https://your-domain</code>.</li>
</ol>
</section></div></body></html>"""


def host_name(request: web.Request) -> str:
    host = request.host or ""
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_local(request: web.Request) -> bool:
    name = host_name(request).lower()
    return name in LOCAL_HOSTS or name.endswith(".localhost")


def is_https(request: web.Request) -> bool:
    if request.secure:
        return True
    forwarded = request.headers.get("X-Forwarded-Proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


def transport_ok(request: web.Request, allow_plain_http: bool) -> bool:
    return allow_plain_http or is_https(request) or is_local(request)


def https_middleware(allow_plain_http: bool):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.path.startswith(PROTECTED_PREFIXES) and not transport_ok(request, allow_plain_http):
            if request.path.startswith(("/setup/api/", "/admin/", "/v1/")):
                return web.json_response(
                    {"error": "https_required", "message": "Use the https:// address of this server"},
                    status=403,
                )
            return web.Response(text=INSECURE_PAGE, content_type="text/html", status=403)
        resp = await handler(request)
        if is_https(request):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    return middleware
